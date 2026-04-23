# tests/test_security_hardening.py
"""Tests for the security hardening work: PathValidator, redact_secrets, DNS."""

import os
import sys


sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from ashyterm.utils.security import (
    HostnameValidator,
    PathValidator,
    SecurityConfig,
    redact_secrets,
)


class TestPathValidator:
    def test_accepts_normal_relative_path(self, tmp_path):
        assert PathValidator.is_safe_path(str(tmp_path / "settings.json"))

    def test_rejects_empty(self):
        assert PathValidator.is_safe_path("") is False

    def test_rejects_control_chars(self):
        assert PathValidator.is_safe_path("a\0b") is False
        assert PathValidator.is_safe_path("a|b") is False

    def test_rejects_parent_traversal_segments(self):
        assert PathValidator.is_safe_path("a/../b") is False
        assert PathValidator.is_safe_path("../secrets") is False

    def test_base_path_containment_allows_inside(self, tmp_path):
        (tmp_path / "a").mkdir()
        target = tmp_path / "a" / "file.txt"
        target.write_text("x")
        assert PathValidator.is_safe_path(str(target), base_path=str(tmp_path))

    def test_base_path_containment_rejects_outside(self, tmp_path):
        # Absolute path outside base_path
        assert PathValidator.is_safe_path("/etc/passwd", base_path=str(tmp_path)) is False

    def test_rejects_too_long_path(self):
        long_path = "/" + "a" * (SecurityConfig.MAX_PATH_LENGTH + 10)
        assert PathValidator.is_safe_path(long_path) is False


class TestRedactSecrets:
    def test_redacts_api_key_field(self):
        out = redact_secrets({"api_key": "secret123", "provider": "groq"})
        assert out["api_key"] == "***redacted***"
        assert out["provider"] == "groq"

    def test_empty_secret_stays_empty(self):
        """Empty strings aren't replaced with the placeholder (signals 'unset')."""
        out = redact_secrets({"api_key": "", "model": "llama"})
        assert out["api_key"] == ""
        assert out["model"] == "llama"

    def test_recurses_into_nested_dict(self):
        out = redact_secrets({"config": {"password": "abc"}})
        assert out["config"]["password"] == "***redacted***"

    def test_preserves_non_secret_keys(self):
        out = redact_secrets({"host": "example.com", "port": 22})
        assert out == {"host": "example.com", "port": 22}

    def test_redacts_bearer_tokens_in_string(self):
        out = redact_secrets(
            "Authorization: Bearer abcdefghij1234567890abcdefABCDEF"
        )
        assert "***redacted***" in out
        assert "abcdefghij1234567890" not in out

    def test_list_of_dicts(self):
        out = redact_secrets([{"api_key": "x"}, {"token": "y"}])
        assert out == [{"api_key": "***redacted***"}, {"token": "***redacted***"}]


class TestHostnameValidator:
    def test_accepts_simple_hostname(self):
        assert HostnameValidator.is_valid_hostname("example.com") is True

    def test_rejects_empty(self):
        assert HostnameValidator.is_valid_hostname("") is False

    def test_rejects_label_too_long(self):
        label = "a" * 64
        assert HostnameValidator.is_valid_hostname(f"{label}.example.com") is False

    def test_private_ip_detection(self):
        assert HostnameValidator.is_private_ip("10.0.0.1") is True
        assert HostnameValidator.is_private_ip("192.168.1.1") is True
        assert HostnameValidator.is_private_ip("8.8.8.8") is False

    def test_resolve_hostname_returns_none_on_bad_name(self):
        # Not a real hostname — must not raise and must return None.
        assert (
            HostnameValidator.resolve_hostname("invalid--hostname.invalid", timeout=1.0)
            is None
        )
