# tests/test_sshpass_cleanup.py
"""Tests for the sshpass password-file lifecycle hardening (C1 + H9)."""

import os
import sys
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


def _make_mixin_with_temp_cache(tmp_path):
    """Instantiate SSHSpawnMixin-compatible object wired to a writable cache_dir."""
    from ashyterm.terminal.ssh_spawn_mixin import SSHSpawnMixin

    class DummyMixin(SSHSpawnMixin):
        def __init__(self, cache_dir):
            import logging

            self.logger = logging.getLogger("ashytest")
            self.platform_info = MagicMock()
            self.platform_info.cache_dir = cache_dir
            self._last_sshpass_file = None
            self.settings_manager = MagicMock()
            self.settings_manager.get = MagicMock(
                side_effect=lambda k, d=None: {"ssh_strict_host_key_checking": "accept-new"}.get(
                    k, d
                )
            )

    return DummyMixin(tmp_path)


class TestSshpassFileCreation:
    def test_create_and_cleanup(self, tmp_path):
        mx = _make_mixin_with_temp_cache(tmp_path)
        path = mx._create_sshpass_file("s3cret")
        assert path is not None
        p = tmp_path / os.path.basename(path)
        assert p.exists()
        assert (p.stat().st_mode & 0o777) == 0o600
        assert p.read_bytes() == b"s3cret"
        mx._last_sshpass_file = path
        mx._cleanup_pending_sshpass_file()
        assert not p.exists()
        assert mx._last_sshpass_file is None

    def test_cleanup_when_file_already_gone(self, tmp_path):
        mx = _make_mixin_with_temp_cache(tmp_path)
        path = mx._create_sshpass_file("s3cret")
        os.unlink(path)
        mx._last_sshpass_file = path
        # Must not raise even if the file is already gone.
        mx._cleanup_pending_sshpass_file()
        assert mx._last_sshpass_file is None


class TestStrictHostKeyChecking:
    def test_accepts_known_values(self, tmp_path):
        mx = _make_mixin_with_temp_cache(tmp_path)
        for value in ("ask", "accept-new", "yes", "no"):
            mx.settings_manager.get = MagicMock(
                side_effect=lambda k, d=None, v=value: v if k == "ssh_strict_host_key_checking" else d
            )
            assert mx._get_strict_host_key_checking() == value

    def test_unknown_value_falls_back(self, tmp_path):
        mx = _make_mixin_with_temp_cache(tmp_path)
        mx.settings_manager.get = MagicMock(
            side_effect=lambda k, d=None: "maybe" if k == "ssh_strict_host_key_checking" else d
        )
        assert mx._get_strict_host_key_checking() == "accept-new"
