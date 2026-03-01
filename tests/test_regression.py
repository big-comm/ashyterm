# tests/test_regression.py
"""
Regression tests for features added during audit implementation.

Covers: JSON versioning, session model serialization, security utilities,
DI constructor patterns, and helper functions that must not regress.
"""

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


# ── JSON Versioning ──────────────────────────────────────────────────────────


class TestJsonVersioning:
    """Tests for utils/json_versioning.py — migration and stamp functions."""

    def test_stamp_version_sets_field(self):
        from ashyterm.utils.json_versioning import stamp_version

        data = {"key": "value"}
        result = stamp_version(data, 3)
        assert result["_version"] == 3
        assert result["key"] == "value"

    def test_stamp_version_overwrites_existing(self):
        from ashyterm.utils.json_versioning import stamp_version

        data = {"_version": 1, "key": "value"}
        result = stamp_version(data, 5)
        assert result["_version"] == 5

    def test_migrate_data_no_migration_needed(self):
        from ashyterm.utils.json_versioning import migrate_data

        data = {"_version": 2, "key": "value"}
        result = migrate_data(data, 2, {})
        assert result is data  # No copy — same object returned
        assert result["_version"] == 2

    def test_migrate_data_from_v0_to_v1(self):
        from ashyterm.utils.json_versioning import migrate_data

        def v0_to_v1(d):
            d["migrated"] = True
            return d

        data = {"key": "old"}
        result = migrate_data(data, 1, {0: v0_to_v1})
        assert result["_version"] == 1
        assert result["migrated"] is True
        assert result["key"] == "old"

    def test_migrate_data_chained_migrations(self):
        from ashyterm.utils.json_versioning import migrate_data

        def v0_to_v1(d):
            d["step1"] = True
            return d

        def v1_to_v2(d):
            d["step2"] = True
            return d

        data = {"key": "old"}
        result = migrate_data(data, 2, {0: v0_to_v1, 1: v1_to_v2})
        assert result["_version"] == 2
        assert result["step1"] is True
        assert result["step2"] is True

    def test_migrate_data_skips_missing_migration_fn(self):
        """Migration from v0→v2 with only v1→v2 defined: v0→v1 is a no-op."""
        from ashyterm.utils.json_versioning import migrate_data

        def v1_to_v2(d):
            d["upgraded"] = True
            return d

        data = {"key": "value"}
        result = migrate_data(data, 2, {1: v1_to_v2})
        assert result["_version"] == 2
        assert result["upgraded"] is True

    def test_migrate_data_newer_version_returns_unchanged(self):
        """Data from a newer version than code should not be downgraded."""
        from ashyterm.utils.json_versioning import migrate_data

        data = {"_version": 99, "key": "future"}
        result = migrate_data(data, 1, {})
        assert result["_version"] == 99
        assert result["key"] == "future"

    def test_migrate_data_without_version_field_assumes_v0(self):
        from ashyterm.utils.json_versioning import migrate_data

        called = []

        def v0_to_v1(d):
            called.append(True)
            return d

        data = {"key": "legacy"}
        result = migrate_data(data, 1, {0: v0_to_v1})
        assert len(called) == 1
        assert result["_version"] == 1


# ── Session Model Serialization ─────────────────────────────────────────────


class TestSessionModelSerialization:
    """Ensure SessionItem and SessionFolder round-trip correctly via to_dict/from_dict."""

    def test_session_item_round_trip(self):
        from ashyterm.sessions.models import SessionItem

        original = SessionItem(
            name="TestSSH",
            host="192.168.1.1",
            port=2222,
            user="admin",
            session_type="ssh",
        )

        data = original.to_dict()
        restored = SessionItem.from_dict(data)

        assert restored.name == "TestSSH"
        assert restored.host == "192.168.1.1"
        assert restored.port == 2222
        assert restored.user == "admin"
        assert restored.session_type == "ssh"

    def test_session_item_default_values(self):
        from ashyterm.sessions.models import SessionItem

        item = SessionItem(name="Defaults")
        data = item.to_dict()
        restored = SessionItem.from_dict(data)

        assert restored.port == 22
        assert restored.session_type == "ssh"

    def test_session_folder_round_trip(self):
        from ashyterm.sessions.models import SessionFolder

        folder = SessionFolder(name="Production", path="/Production")

        data = folder.to_dict()
        restored = SessionFolder.from_dict(data)

        assert restored.name == "Production"
        assert restored.path == "/Production"

    def test_session_item_is_ssh_detection(self):
        from ashyterm.sessions.models import SessionItem

        ssh_item = SessionItem(
            name="SSH", session_type="ssh", host="server.example.com"
        )
        assert ssh_item.is_ssh() is True

        local_item = SessionItem(name="Local", session_type="local")
        assert local_item.is_ssh() is False

    def test_session_item_to_dict_contains_expected_keys(self):
        from ashyterm.sessions.models import SessionItem

        item = SessionItem(name="Test")
        data = item.to_dict()

        required_keys = {"name", "host", "port", "user", "session_type"}
        assert required_keys.issubset(set(data.keys()))


# ── Security Utilities ───────────────────────────────────────────────────────


class TestInputSanitizer:
    """Tests for InputSanitizer to prevent regressions in input validation."""

    def test_sanitize_filename_strips_dangerous_chars(self):
        from ashyterm.utils.security import InputSanitizer

        result = InputSanitizer.sanitize_filename('<script>alert("xss")</script>')
        assert "<" not in result
        assert ">" not in result

    def test_sanitize_filename_preserves_valid_input(self):
        from ashyterm.utils.security import InputSanitizer

        result = InputSanitizer.sanitize_filename("My Server 2024")
        assert "My Server 2024" in result

    def test_sanitize_filename_handles_empty(self):
        from ashyterm.utils.security import InputSanitizer

        result = InputSanitizer.sanitize_filename("")
        assert isinstance(result, str)

    def test_sanitize_hostname_basic(self):
        from ashyterm.utils.security import InputSanitizer

        result = InputSanitizer.sanitize_hostname("server.example.com")
        assert result == "server.example.com"

    def test_hostname_validator(self):
        from ashyterm.utils.security import HostnameValidator

        assert HostnameValidator.is_valid_hostname("server.example.com") is True
        assert HostnameValidator.is_valid_hostname("") is False

    def test_path_validator_rejects_traversal(self):
        from ashyterm.utils.security import PathValidator

        assert PathValidator.is_safe_path("../../../etc/passwd") is False


# ── OSC7 Parsing ─────────────────────────────────────────────────────────────


class TestOSC7Regression:
    """Regression tests for OSC7 parsing — path display and edge cases."""

    def test_parse_directory_uri_basic(self):
        from ashyterm.utils.osc7 import parse_directory_uri

        result = parse_directory_uri("file://localhost/home/user")
        assert result is not None
        assert result.path == "/home/user"

    def test_parse_directory_uri_none_input(self):
        from ashyterm.utils.osc7 import parse_directory_uri

        assert parse_directory_uri(None) is None

    def test_parse_directory_uri_empty_string(self):
        from ashyterm.utils.osc7 import parse_directory_uri

        assert parse_directory_uri("") is None

    def test_parse_directory_uri_non_file_scheme(self):
        from ashyterm.utils.osc7 import parse_directory_uri

        assert parse_directory_uri("https://example.com/path") is None

    def test_parse_directory_uri_encoded_spaces(self):
        from ashyterm.utils.osc7 import parse_directory_uri

        result = parse_directory_uri("file://host/home/my%20folder")
        assert result is not None
        assert " " in result.path


# ── Logger Utility ───────────────────────────────────────────────────────────


class TestLoggerUtility:
    """Tests for logger module to verify level configuration doesn't regress."""

    def test_get_logger_returns_logger(self):
        from ashyterm.utils.logger import get_logger

        log = get_logger("test.module")
        assert log is not None
        assert hasattr(log, "info")
        assert hasattr(log, "error")

    def test_get_logger_consistent_instance(self):
        from ashyterm.utils.logger import get_logger

        log1 = get_logger("test.same")
        log2 = get_logger("test.same")
        assert log1 is log2


# ── Backup Utility ───────────────────────────────────────────────────────────


class TestBackupUtility:
    """Tests for backup module import and instantiation."""

    def test_backup_manager_importable(self):
        from ashyterm.utils.backup import BackupManager, get_backup_manager

        assert BackupManager is not None
        assert callable(get_backup_manager)


# ── RE Engine ────────────────────────────────────────────────────────────────


class TestReEngine:
    """Tests for the regex engine utility."""

    def test_engine_is_re_compatible(self):
        from ashyterm.utils.re_engine import engine

        # Must support standard re interface
        assert hasattr(engine, "compile")
        assert hasattr(engine, "search")
        assert hasattr(engine, "match")

    def test_engine_compile_and_search(self):
        from ashyterm.utils.re_engine import engine

        pattern = engine.compile(r"\d+")
        match = pattern.search("abc123def")
        assert match is not None
        assert match.group() == "123"

    def test_engine_invalid_pattern_raises(self):
        from ashyterm.utils.re_engine import engine

        with pytest.raises(Exception):
            engine.compile(r"[invalid")


# ── DI Constructor Patterns ──────────────────────────────────────────────────


class TestDIPatterns:
    """Verify DI constructor signatures accept optional dependencies."""

    def test_process_spawner_accepts_settings_manager(self):
        """ProcessSpawner should accept an optional settings_manager parameter."""
        import inspect
        from ashyterm.terminal.spawner import ProcessSpawner

        sig = inspect.signature(ProcessSpawner.__init__)
        assert "settings_manager" in sig.parameters
        param = sig.parameters["settings_manager"]
        assert param.default is None

    def test_file_operations_accepts_spawner(self):
        """FileOperations should accept an optional spawner parameter."""
        import inspect
        from ashyterm.filemanager.operations import FileOperations

        sig = inspect.signature(FileOperations.__init__)
        assert "spawner" in sig.parameters
        param = sig.parameters["spawner"]
        assert param.default is None


# ── Session Validation ───────────────────────────────────────────────────────


class TestSessionValidation:
    """Validate session validation functions don't regress."""

    def test_validate_session_for_add_importable(self):
        from ashyterm.sessions.validation import validate_session_for_add

        assert callable(validate_session_for_add)

    def test_validate_folder_for_add_importable(self):
        from ashyterm.sessions.validation import validate_folder_for_add

        assert callable(validate_folder_for_add)


# ── Translation Utils ────────────────────────────────────────────────────────


class TestTranslationUtils:
    """Verify translation utility doesn't break string operations."""

    def test_underscore_function_returns_string(self):
        from ashyterm.utils.translation_utils import _

        result = _("Hello World")
        assert isinstance(result, str)
        assert len(result) > 0

    def test_underscore_function_format(self):
        from ashyterm.utils.translation_utils import _

        result = _("Count: {n}").format(n=42)
        assert "42" in result


# ── SSH Config Parser ────────────────────────────────────────────────────────


class TestSSHConfigParser:
    """Tests for SSH config file parsing utility."""

    def test_parse_nonexistent_config(self):
        from ashyterm.utils.ssh_config_parser import SSHConfigParser

        parser = SSHConfigParser()
        # parse() should handle nonexistent config gracefully
        entries = parser.parse(Path("/nonexistent/ssh/config"))
        assert isinstance(entries, list)
        assert len(entries) == 0


# ── Platform Utility ─────────────────────────────────────────────────────────


class TestPlatformUtility:
    """Tests for platform detection utilities."""

    def test_platform_info_importable(self):
        from ashyterm.utils.platform import PlatformInfo

        info = PlatformInfo()
        assert isinstance(info.architecture, str)

    def test_platform_info_config_directory(self):
        from ashyterm.utils.platform import PlatformInfo

        info = PlatformInfo()
        config_dir = info.config_dir
        assert config_dir is not None
