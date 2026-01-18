# tests/test_qa.py
"""
Basic unit tests for Ashy Terminal core functionality.

These tests cover utility functions and core logic that can be tested
without GTK initialization.
"""

import os
import sys

import pytest

# Add src to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


class TestHelpers:
    """Tests for helper functions in ashyterm.helpers module."""

    def test_is_valid_url_http(self):
        """Test that HTTP URLs are recognized as valid."""
        from ashyterm.helpers import is_valid_url

        assert is_valid_url("http://example.com") is True
        assert is_valid_url("https://example.com") is True
        assert is_valid_url("https://example.com/path?query=1") is True

    def test_is_valid_url_invalid(self):
        """Test that invalid strings are not recognized as URLs."""
        from ashyterm.helpers import is_valid_url

        assert is_valid_url("not a url") is False
        assert is_valid_url("example.com") is False  # Missing scheme
        assert is_valid_url("") is False
        # Note: ftp:// is valid per the implementation

    def test_generate_unique_name(self):
        """Test unique name generation."""
        from ashyterm.helpers import generate_unique_name

        existing = ["Terminal 1", "Terminal 2"]
        new_name = generate_unique_name("Terminal", existing)
        assert new_name not in existing
        assert new_name.startswith("Terminal")

    def test_generate_unique_name_empty_list(self):
        """Test unique name generation with empty list."""
        from ashyterm.helpers import generate_unique_name

        new_name = generate_unique_name("Session", [])
        # With empty list, base name is returned unchanged
        assert new_name == "Session"


class TestSecurityUtilities:
    """Tests for security validation utilities."""

    def test_hostname_sanitizer(self):
        """Test hostname sanitization."""
        from ashyterm.utils.security import InputSanitizer

        # Valid hostnames should pass through
        assert InputSanitizer.sanitize_hostname("example.com") == "example.com"
        assert InputSanitizer.sanitize_hostname("my-server.local") == "my-server.local"

    def test_hostname_sanitizer_removes_invalid_chars(self):
        """Test that invalid characters are removed from hostnames."""
        from ashyterm.utils.security import InputSanitizer

        # Should remove invalid characters
        result = InputSanitizer.sanitize_hostname("host<name>")
        assert "<" not in result
        assert ">" not in result

    def test_hostname_validator_valid(self):
        """Test hostname validation for valid hostnames."""
        from ashyterm.utils.security import HostnameValidator

        assert HostnameValidator.is_valid_hostname("example.com") is True
        assert HostnameValidator.is_valid_hostname("192.168.1.1") is True
        assert HostnameValidator.is_valid_hostname("my-server") is True

    def test_hostname_validator_invalid(self):
        """Test hostname validation for invalid hostnames."""
        from ashyterm.utils.security import HostnameValidator

        assert HostnameValidator.is_valid_hostname("") is False
        assert HostnameValidator.is_valid_hostname("-invalid") is False

    def test_path_validator_safe(self):
        """Test path validation for safe paths."""
        from ashyterm.utils.security import PathValidator

        assert PathValidator.is_safe_path("/home/user/file.txt") is True
        assert PathValidator.is_safe_path("./relative/path") is True

    def test_path_validator_unsafe(self):
        """Test path validation for unsafe paths."""
        from ashyterm.utils.security import PathValidator

        # Path traversal attempts should be rejected
        assert PathValidator.is_safe_path("../../../etc/passwd") is False
        # Null bytes should be rejected
        assert PathValidator.is_safe_path("file\x00name") is False


class TestSessionValidation:
    """Tests for session data validation."""

    def test_validate_session_data_valid_local(self):
        """Test validation of valid local session data."""
        from ashyterm.utils.security import validate_session_data

        data = {
            "name": "Local Session",
            "session_type": "local",
        }
        is_valid, errors = validate_session_data(data)
        assert is_valid is True
        assert len(errors) == 0

    def test_validate_session_data_valid_ssh(self):
        """Test validation of valid SSH session data."""
        from ashyterm.utils.security import validate_session_data

        data = {
            "name": "SSH Session",
            "session_type": "ssh",
            "host": "example.com",
            "user": "admin",  # Note: 'user' not 'username'
            "port": 22,
        }
        is_valid, errors = validate_session_data(data)
        assert is_valid is True
        assert len(errors) == 0

    def test_validate_session_data_missing_user(self):
        """Test that SSH session without user is invalid."""
        from ashyterm.utils.security import validate_session_data

        data = {
            "name": "Bad SSH Session",
            "session_type": "ssh",
            "host": "example.com",  # Host is present
            # Missing 'user' - should fail
            "port": 22,
        }
        is_valid, errors = validate_session_data(data)
        assert is_valid is False
        assert len(errors) > 0


class TestTranslation:
    """Tests for translation utilities."""

    def test_translation_function_exists(self):
        """Test that translation function is properly exported."""
        from ashyterm.utils.translation_utils import _

        # Should be callable
        assert callable(_)
        # Should return a string for string input
        result = _("Test string")
        assert isinstance(result, str)


class TestExceptions:
    """Tests for custom exception classes."""

    def test_ashy_terminal_error_basic(self):
        """Test basic exception creation."""
        from ashyterm.utils.exceptions import AshyTerminalError, ErrorCategory

        error = AshyTerminalError("Test error message", category=ErrorCategory.SYSTEM)
        assert "Test error message" in str(error)

    def test_validation_error_with_field(self):
        """Test ValidationError with field context."""
        from ashyterm.utils.exceptions import ValidationError

        error = ValidationError(
            message="Invalid value",
            field="hostname",
            reason="Cannot be empty",
        )
        assert "hostname" in str(error) or "Invalid" in str(error)


class TestLogger:
    """Tests for logging utilities."""

    def test_get_logger_returns_logger(self):
        """Test that get_logger returns a logger instance."""
        from ashyterm.utils.logger import get_logger

        logger = get_logger("test.module")
        assert logger is not None
        # Should have standard logging methods
        assert hasattr(logger, "info")
        assert hasattr(logger, "error")
        assert hasattr(logger, "debug")

    def test_get_logger_without_name(self):
        """Test that get_logger works without explicit name."""
        from ashyterm.utils.logger import get_logger

        logger = get_logger()
        assert logger is not None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
