# tests/test_atomic_json_write.py
"""Tests for ashyterm.utils.security.atomic_json_write."""

import json
import os
import sys
import threading

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from ashyterm.utils.security import atomic_json_write


class TestAtomicJsonWrite:
    def test_writes_valid_json(self, tmp_path):
        dst = tmp_path / "settings.json"
        atomic_json_write(dst, {"color_scheme": 1, "font": "Mono 10"})
        assert json.loads(dst.read_text()) == {"color_scheme": 1, "font": "Mono 10"}

    def test_sets_secure_permissions_when_requested(self, tmp_path):
        dst = tmp_path / "settings.json"
        atomic_json_write(dst, {"k": "v"}, secure_permissions=True)
        mode = dst.stat().st_mode & 0o777
        assert mode == 0o600

    def test_skips_chmod_when_disabled(self, tmp_path):
        dst = tmp_path / "settings.json"
        atomic_json_write(dst, {"k": "v"}, secure_permissions=False)
        # Should be whatever the umask defaults allow — not forced to 0600.
        assert dst.exists()

    def test_creates_parent_directories(self, tmp_path):
        dst = tmp_path / "nested" / "deeper" / "out.json"
        atomic_json_write(dst, {"k": 1})
        assert dst.exists()

    def test_no_leftover_temp_file_on_success(self, tmp_path):
        dst = tmp_path / "out.json"
        atomic_json_write(dst, {"k": 1})
        leftover = [p for p in tmp_path.iterdir() if p.name.startswith(".out.json.")]
        assert leftover == []

    def test_cleans_up_temp_file_on_failure(self, tmp_path, monkeypatch):
        """If json.dump raises, the temp file must be removed."""
        dst = tmp_path / "out.json"

        original_dump = json.dump

        def boom(obj, fp, **kwargs):
            raise ValueError("boom")

        monkeypatch.setattr(json, "dump", boom)
        with pytest.raises(ValueError):
            atomic_json_write(dst, {"k": 1})
        monkeypatch.setattr(json, "dump", original_dump)

        leftover = [p for p in tmp_path.iterdir() if p.name.startswith(".out.json.")]
        assert leftover == []

    def test_concurrent_writes_no_race(self, tmp_path):
        """Concurrent writers never clobber each other's temp files."""
        dst = tmp_path / "out.json"

        def writer(value):
            atomic_json_write(dst, {"v": value})

        threads = [threading.Thread(target=writer, args=(i,)) for i in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # Only the winning payload is visible; temp files must be gone.
        payload = json.loads(dst.read_text())
        assert "v" in payload
        leftover = [p for p in tmp_path.iterdir() if p.name.startswith(".out.json.")]
        assert leftover == []

    def test_overwrite_preserves_original_on_partial_failure(
        self, tmp_path, monkeypatch
    ):
        """If the tmp write fails, the previous file must still be intact."""
        dst = tmp_path / "out.json"
        atomic_json_write(dst, {"v": "original"})

        original_dump = json.dump

        def boom(obj, fp, **kwargs):
            raise ValueError("boom")

        monkeypatch.setattr(json, "dump", boom)
        with pytest.raises(ValueError):
            atomic_json_write(dst, {"v": "corrupted"})
        monkeypatch.setattr(json, "dump", original_dump)

        assert json.loads(dst.read_text()) == {"v": "original"}
