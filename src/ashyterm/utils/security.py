# ashyterm/utils/security.py

import ipaddress
import os
import re
import socket
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .exceptions import (
    DirectoryPermissionError,
    FilePermissionError,
    HostnameValidationError,
    PathValidationError,
    SSHKeyError,
)
from .logger import get_logger
from .translation_utils import _

# Pre-compiled patterns for hostname validation/sanitization
_HOSTNAME_SANITIZE_PATTERN = re.compile(r"[^a-z0-9.-]")
_HOSTNAME_VALID_PATTERN = re.compile(r"^[a-zA-Z0-9.-]+$")


class SecurityConfig:
    """Security configuration and limits."""

    MAX_HOSTNAME_LENGTH = 253
    MAX_USERNAME_LENGTH = 32
    MAX_SSH_KEY_SIZE = 16384
    MAX_PATH_LENGTH = 4096
    # Control and reserved characters rejected anywhere in a session path.
    # '/' and ':' are legitimate in Unix paths so they are NOT included.
    FORBIDDEN_PATH_CHARS = ["<", ">", '"', "|", "?", "*", "\0"]
    MAX_SESSION_NAME_LENGTH = 128
    SECURE_FILE_PERMISSIONS = 0o600
    SECURE_DIR_PERMISSIONS = 0o700


class InputSanitizer:
    """Input sanitization utilities."""

    @staticmethod
    def sanitize_filename(filename: str, replacement: str = "_") -> str:
        if not filename:
            return _("unnamed")
        forbidden_chars = '<>:"/\\|?*\0'
        sanitized = filename
        for char in forbidden_chars:
            sanitized = sanitized.replace(char, replacement)
        sanitized = "".join(char for char in sanitized if ord(char) >= 32)
        sanitized = sanitized.strip(" .")
        if not sanitized:
            sanitized = _("unnamed")
        if len(sanitized) > SecurityConfig.MAX_SESSION_NAME_LENGTH:
            sanitized = sanitized[: SecurityConfig.MAX_SESSION_NAME_LENGTH]
        return sanitized

    @staticmethod
    def sanitize_hostname(hostname: str) -> str:
        if not hostname:
            return ""
        sanitized = hostname.strip().lower()
        sanitized = _HOSTNAME_SANITIZE_PATTERN.sub("", sanitized)
        if len(sanitized) > SecurityConfig.MAX_HOSTNAME_LENGTH:
            sanitized = sanitized[: SecurityConfig.MAX_HOSTNAME_LENGTH]
        return sanitized


class HostnameValidator:
    """Hostname validation utilities."""

    @staticmethod
    def is_valid_hostname(hostname: str) -> bool:
        if not hostname or len(hostname) > SecurityConfig.MAX_HOSTNAME_LENGTH:
            return False
        if not _HOSTNAME_VALID_PATTERN.match(hostname):
            return False
        labels = hostname.split(".")
        for label in labels:
            if (
                not label
                or len(label) > 63
                or label.startswith("-")
                or label.endswith("-")
            ):
                return False
        return True

    @staticmethod
    def is_private_ip(ip_str: str) -> bool:
        try:
            return ipaddress.ip_address(ip_str).is_private
        except ValueError:
            return False

    @staticmethod
    def resolve_hostname(hostname: str, timeout: float = 5.0) -> Optional[str]:
        """Resolve a hostname to an IP address.

        Runs socket.gethostbyname in a worker thread so the timeout works
        regardless of which thread called us (SIGALRM only works in the
        main thread of the main interpreter and is therefore unsafe here).

        Args:
            hostname: The hostname to resolve
            timeout: Resolution timeout in seconds

        Returns:
            The resolved IP address or None if resolution fails
        """
        import concurrent.futures

        logger = get_logger("ashyterm.security")

        def _resolve():
            return socket.gethostbyname(hostname)

        try:
            with concurrent.futures.ThreadPoolExecutor(
                max_workers=1, thread_name_prefix="ashy-dns"
            ) as executor:
                future = executor.submit(_resolve)
                try:
                    return future.result(timeout=timeout)
                except concurrent.futures.TimeoutError:
                    logger.debug(
                        f"Hostname resolution timed out for {hostname} after {timeout}s"
                    )
                    future.cancel()
                    return None
        except socket.gaierror as e:
            logger.debug(f"Hostname resolution failed for {hostname}: {e}")
            return None
        except Exception as e:
            logger.debug(f"Unexpected error resolving hostname {hostname}: {e}")
            return None


class SSHKeyValidator:
    """SSH key validation utilities."""

    @staticmethod
    def validate_ssh_key_path(key_path: str) -> Tuple[bool, Optional[str]]:
        if not key_path:
            return False, _("Key path is empty")
        try:
            path = Path(key_path)
            if not path.exists():
                return False, _("Key file does not exist: {}").format(key_path)
            if not path.is_file():
                return False, _("Key path is not a file: {}").format(key_path)
            file_size = path.stat().st_size
            if file_size > SecurityConfig.MAX_SSH_KEY_SIZE:
                return False, _("Key file too large: {} bytes").format(file_size)
            if file_size == 0:
                return False, _("Key file is empty")
            if path.stat().st_mode & 0o077:
                return False, _("Key file has insecure permissions (should be 600)")
            if not os.access(path, os.R_OK):
                return False, _("Key file is not readable")
            return True, None
        except OSError as e:
            return False, _("Error accessing key file: {}").format(e)

    # Valid SSH key format markers
    _PEM_MARKERS = (
        b"-----BEGIN RSA PRIVATE KEY-----",
        b"-----BEGIN DSA PRIVATE KEY-----",
        b"-----BEGIN EC PRIVATE KEY-----",
        b"-----BEGIN PRIVATE KEY-----",
        b"-----BEGIN ENCRYPTED PRIVATE KEY-----",
        b"-----BEGIN OPENSSH PRIVATE KEY-----",
    )
    _OPENSSH_MARKER = b"openssh-key-v1"
    _PUBLIC_KEY_PREFIXES = (
        b"ssh-rsa ",
        b"ssh-dss ",
        b"ssh-ed25519 ",
        b"ecdsa-sha2-",
        b"sk-ssh-ed25519@",
        b"sk-ecdsa-sha2-",
    )

    @staticmethod
    def read_and_validate_ssh_key(
        key_path: str,
    ) -> Tuple[bool, Optional[str], Optional[str]]:
        path_valid, path_error = SSHKeyValidator.validate_ssh_key_path(key_path)
        if not path_valid:
            return False, path_error, None

        try:
            with open(key_path, "rb") as f:
                header = f.read(128)

            if not header:
                return False, _("Key file is empty"), None

            # Check for PEM-encoded private keys
            for marker in SSHKeyValidator._PEM_MARKERS:
                if header.startswith(marker):
                    return True, None, None

            # Check for binary OpenSSH format
            if SSHKeyValidator._OPENSSH_MARKER in header:
                return True, None, None

            # Check for public keys (less common as identity files)
            for prefix in SSHKeyValidator._PUBLIC_KEY_PREFIXES:
                if header.startswith(prefix):
                    return True, None, None

            return (
                False,
                _("File does not appear to be a valid SSH key"),
                None,
            )
        except OSError as e:
            return False, _("Error reading key file: {}").format(e), None


class PathValidator:
    """File path validation utilities."""

    @staticmethod
    def is_safe_path(path: str, base_path: Optional[str] = None) -> bool:
        """Return True when *path* looks safe to use as a filesystem path.

        Rejects:
        - Empty / overly long paths
        - Paths that contain forbidden control characters
        - Paths whose resolved form escapes *base_path* (when given)

        When *base_path* is given, uses pathlib.Path.resolve(strict=False)
        followed by Path.is_relative_to(...). This correctly handles '..'
        segments, symlinks that point outside the base, and absolute paths
        that happen to normalise to something inside the base.
        """
        if not path:
            return False
        try:
            if len(path) > SecurityConfig.MAX_PATH_LENGTH:
                return False

            for char in SecurityConfig.FORBIDDEN_PATH_CHARS:
                if char in path:
                    return False

            # Reject raw '..' path components even without a base_path:
            # callers that treat the value as a relative path should not
            # have traversal segments smuggled in.
            parts = Path(path).parts
            if ".." in parts:
                return False

            if base_path:
                base_resolved = Path(base_path).resolve(strict=False)
                candidate_resolved = Path(path).resolve(strict=False)
                try:
                    candidate_resolved.relative_to(base_resolved)
                except ValueError:
                    return False

            return True
        except Exception:
            return False


class SecurityAuditor:
    """Security auditing utilities."""

    def __init__(self):
        self.logger = get_logger("ashyterm.security.audit")

    def audit_ssh_session(
        self, session_data: Dict[str, Any], resolve_dns: bool = False
    ) -> List[Dict[str, Any]]:
        """Audit SSH session configuration for security issues.

        Args:
            session_data: Session configuration dictionary
            resolve_dns: Whether to perform DNS resolution (can be slow/blocking).
                         Should only be True when explicitly testing connection.

        Returns:
            List of security findings
        """
        findings = []
        hostname = session_data.get("host", "")
        if hostname:
            if not HostnameValidator.is_valid_hostname(hostname):
                findings.append(
                    {
                        "severity": "medium",
                        "type": "invalid_hostname",
                        "message": _("Invalid hostname format: {}").format(hostname),
                        "recommendation": _("Use a valid hostname or IP address"),
                    }
                )
            elif resolve_dns:
                # Only resolve hostname when explicitly requested (e.g., test connection)
                # to avoid blocking the UI during startup
                if (
                    ip := HostnameValidator.resolve_hostname(hostname)
                ) and HostnameValidator.is_private_ip(ip):
                    findings.append(
                        {
                            "severity": "low",
                            "type": "private_ip",
                            "message": _("Connecting to private IP: {}").format(ip),
                            "recommendation": _("Ensure this is intentional"),
                        }
                    )

        auth_type = session_data.get("auth_type", "")
        auth_value = session_data.get("auth_value", "")
        if auth_type == "key" and auth_value:
            is_valid, error = SSHKeyValidator.validate_ssh_key_path(auth_value)
            if not is_valid:
                findings.append(
                    {
                        "severity": "high",
                        "type": "invalid_ssh_key",
                        "message": _("SSH key validation failed: {}").format(error),
                        "recommendation": _("Fix SSH key configuration"),
                    }
                )
        elif auth_type == "password":
            findings.append(
                {
                    "severity": "medium",
                    "type": "password_auth",
                    "message": _("Using password authentication"),
                    "recommendation": _(
                        "Consider using SSH key authentication for better security"
                    ),
                }
            )

        username = session_data.get("user", "")
        if username == "root":
            findings.append(
                {
                    "severity": "medium",
                    "type": "root_user",
                    "message": _("Connecting as root user"),
                    "recommendation": _("Use a regular user account when possible"),
                }
            )

        return findings


def validate_ssh_hostname(hostname: str) -> None:
    if not hostname:
        raise HostnameValidationError("", _("Hostname cannot be empty"))
    sanitized = InputSanitizer.sanitize_hostname(hostname)
    if not HostnameValidator.is_valid_hostname(sanitized):
        raise HostnameValidationError(hostname, _("Invalid hostname format"))


def validate_ssh_key_file(key_path: str) -> None:
    is_valid, error, _key = SSHKeyValidator.read_and_validate_ssh_key(key_path)
    if not is_valid:
        raise SSHKeyError(key_path, error or _("Unknown validation error"))


def validate_file_path(file_path: str, base_path: Optional[str] = None) -> None:
    if not PathValidator.is_safe_path(file_path, base_path):
        raise PathValidationError(file_path, _("Path contains unsafe elements"))


def ensure_secure_file_permissions(file_path: str) -> None:
    try:
        Path(file_path).chmod(SecurityConfig.SECURE_FILE_PERMISSIONS)
    except OSError as e:
        raise FilePermissionError(
            file_path, _("set secure permissions"), details={"reason": str(e)}
        )


def atomic_json_write(
    file_path: Path,
    data: dict,
    indent: int = 2,
    ensure_ascii: bool = False,
    secure_permissions: bool = True,
) -> None:
    """Write JSON data atomically using a unique temp file + rename.

    Each call uses tempfile.NamedTemporaryFile (unique suffix) so concurrent
    writers cannot race over a single ``<name>.tmp`` path. Rename is atomic
    on POSIX within the same filesystem.

    Args:
        file_path: Path to the destination JSON file.
        data: Dictionary to serialize as JSON.
        indent: JSON indentation level.
        ensure_ascii: If True, escape non-ASCII characters.
        secure_permissions: If True, set secure file permissions (0o600).

    Raises:
        OSError: If file operations fail.
    """
    import json
    import tempfile

    logger = get_logger("ashyterm.security.atomic_write")

    file_path.parent.mkdir(parents=True, exist_ok=True)

    # delete=False so we can rename it into place before cleanup runs.
    tmp = tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=str(file_path.parent),
        prefix=f".{file_path.name}.",
        suffix=".tmp",
        delete=False,
    )
    tmp_path = Path(tmp.name)
    try:
        with tmp:
            json.dump(data, tmp, indent=indent, ensure_ascii=ensure_ascii)
            tmp.flush()
            try:
                os.fsync(tmp.fileno())
            except OSError:
                pass
        tmp_path.replace(file_path)
        tmp_path = None  # ownership transferred
        if secure_permissions:
            try:
                file_path.chmod(SecurityConfig.SECURE_FILE_PERMISSIONS)
            except OSError as exc:
                logger.debug(f"Could not chmod {file_path}: {exc}")
    finally:
        if tmp_path is not None:
            try:
                tmp_path.unlink()
            except OSError as exc:
                logger.debug(f"Could not remove leftover temp file {tmp_path}: {exc}")


def ensure_secure_directory_permissions(dir_path: str) -> None:
    try:
        Path(dir_path).chmod(SecurityConfig.SECURE_DIR_PERMISSIONS)
    except OSError as e:
        raise DirectoryPermissionError(
            dir_path, _("set secure permissions"), details={"reason": str(e)}
        )


def create_security_auditor() -> SecurityAuditor:
    """Create a new security auditor instance."""
    return SecurityAuditor()


_SECRET_KEY_SUBSTRINGS = (
    "api_key",
    "apikey",
    "password",
    "passwd",
    "secret",
    "token",
    "authorization",
)
_REDACTED = "***redacted***"


def redact_secrets(value: Any) -> Any:
    """Return a copy of *value* with likely secret fields masked.

    Use on dicts/strings/exceptions before feeding them to a logger or
    user-facing dialog. Detection is key-name based, so the caller doesn't
    need to know which providers are in play.
    """
    if isinstance(value, dict):
        redacted: Dict[Any, Any] = {}
        for k, v in value.items():
            key_lower = str(k).lower()
            if any(hint in key_lower for hint in _SECRET_KEY_SUBSTRINGS):
                redacted[k] = _REDACTED if v else v
            else:
                redacted[k] = redact_secrets(v)
        return redacted
    if isinstance(value, (list, tuple)):
        cls = type(value)
        return cls(redact_secrets(v) for v in value)
    if isinstance(value, str):
        # Scrub obvious "Authorization: Bearer <token>" style strings
        import re as _re

        return _re.sub(
            r"(Bearer\s+)([A-Za-z0-9\-._~+/]{8,})",
            r"\1" + _REDACTED,
            value,
            flags=_re.IGNORECASE,
        )
    return value


def _validate_session_name(session_data: Dict[str, Any], errors: List[str]) -> None:
    """Validate session name field."""
    name = session_data.get("name", "")
    if not name or not name.strip():
        errors.append(_("Session name cannot be empty"))
    elif len(name) > SecurityConfig.MAX_SESSION_NAME_LENGTH:
        errors.append(
            _("Session name too long (max {} characters)").format(
                SecurityConfig.MAX_SESSION_NAME_LENGTH
            )
        )


def _validate_hostname(session_data: Dict[str, Any], errors: List[str]) -> str:
    """Validate hostname field. Returns the host value."""
    host = session_data.get("host", "")
    if host:
        if not host.strip():
            errors.append(_("Hostname cannot be empty for SSH sessions"))
        elif not HostnameValidator.is_valid_hostname(host.strip()):
            errors.append(_("Invalid hostname format: {}").format(host))
    return host


def _validate_username(
    session_data: Dict[str, Any], host: str, errors: List[str]
) -> None:
    """Validate username field."""
    username = session_data.get("user", "")
    if host and not username:
        errors.append(_("Username is required for SSH sessions"))
    elif username and len(username) > SecurityConfig.MAX_USERNAME_LENGTH:
        errors.append(
            _("Username too long (max {} characters)").format(
                SecurityConfig.MAX_USERNAME_LENGTH
            )
        )


def _validate_port(session_data: Dict[str, Any], errors: List[str]) -> None:
    """Validate port field."""
    port = session_data.get("port", 22)
    if port is not None:
        try:
            if not (1 <= int(port) <= 65535):
                errors.append(_("Port must be between 1 and 65535"))
        except (ValueError, TypeError):
            errors.append(_("Port must be a valid number"))


def _validate_auth(session_data: Dict[str, Any], host: str, errors: List[str]) -> None:
    """Validate authentication configuration."""
    auth_type = session_data.get("auth_type", "")
    auth_value = session_data.get("auth_value", "")
    if host:
        if auth_type == "key" and auth_value:
            is_key_valid, key_error = SSHKeyValidator.validate_ssh_key_path(auth_value)
            if not is_key_valid:
                errors.append(_("SSH key validation failed: {}").format(key_error))
        elif auth_type not in ["key", "password", ""]:
            errors.append(_("Invalid authentication type: {}").format(auth_type))


def _validate_folder_path(session_data: Dict[str, Any], errors: List[str]) -> None:
    """Validate folder path field."""
    if folder_path := session_data.get("folder_path", ""):
        if not PathValidator.is_safe_path(folder_path):
            errors.append(_("Invalid or unsafe folder path"))


def validate_session_data(session_data: Dict[str, Any]) -> Tuple[bool, List[str]]:
    """Validate session data structure and values."""
    errors: List[str] = []
    try:
        _validate_session_name(session_data, errors)
        host = _validate_hostname(session_data, errors)
        _validate_username(session_data, host, errors)
        _validate_port(session_data, errors)
        _validate_auth(session_data, host, errors)
        _validate_folder_path(session_data, errors)
        return len(errors) == 0, errors
    except Exception as e:
        logger = get_logger("ashyterm.security.validation")
        logger.error(f"Session validation error: {e}")
        return False, [_("Validation error: {}").format(e)]
