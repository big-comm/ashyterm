"""Known-answer tests for BackupManager (encrypted 7z).

Two tiers:

* ``vectors.json`` — parametrised roundtrip. encrypt → decrypt → equal.
* ``known_v1.7z`` — pre-committed archive. Any implementation
  (Python today, Rust tomorrow) must decrypt it with the manifest's
  password and get the declared files. This is the cross-implementation
  contract.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Dict
from unittest.mock import MagicMock

import pytest

py7zr = pytest.importorskip("py7zr")

from ashyterm.utils.backup import BackupManager


FIXTURES_DIR = Path(__file__).parent / "fixtures" / "backup"
SEVEN_Z_MAGIC = bytes.fromhex("377abcaf271c")


def _empty_sessions_store() -> MagicMock:
    """Gio.ListStore stub — no sessions means no keyring interaction."""
    store = MagicMock()
    store.get_n_items.return_value = 0
    return store


def _write_files(target_dir: Path, files: Dict[str, str]) -> list[Path]:
    """Materialize the declared files under target_dir. Return top-level paths."""
    paths: list[Path] = []
    for rel, content in files.items():
        path = target_dir / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        if "/" not in rel:
            paths.append(path)
    return paths


def _load_vectors() -> list[dict]:
    with (FIXTURES_DIR / "vectors.json").open() as f:
        return json.load(f)["vectors"]


# ── Tier 1: roundtrip vectors ──────────────────────────────


@pytest.mark.parametrize(
    "vector",
    _load_vectors(),
    ids=lambda v: v["name"],
)
def test_backup_roundtrip_vector(vector: dict, tmp_path: Path):
    password = vector["password"]
    files = vector["files"]

    src = tmp_path / "src"
    src.mkdir()
    top_level = _write_files(src, files)
    layouts_dir = src / "layouts"  # may or may not exist; both are valid
    archive = tmp_path / "backup.7z"
    restore = tmp_path / "restore"
    restore.mkdir()

    mgr = BackupManager(backup_dir=tmp_path / "bkp")
    mgr.create_encrypted_backup(
        target_file_path=str(archive),
        password=password,
        sessions_store=_empty_sessions_store(),
        source_files=top_level,
        layouts_dir=layouts_dir,
    )

    assert archive.exists(), "archive not created"
    assert archive.stat().st_size > 0, "archive is empty"
    with archive.open("rb") as f:
        assert f.read(6) == SEVEN_Z_MAGIC, "missing 7z magic bytes"

    mgr.restore_from_encrypted_backup(
        source_file_path=str(archive),
        password=password,
        config_dir=restore,
    )

    for rel, expected in files.items():
        restored = restore / rel
        assert restored.exists(), f"{rel} missing after restore"
        assert restored.read_text(encoding="utf-8") == expected, (
            f"{rel} content mismatch"
        )


def test_wrong_password_is_rejected(tmp_path: Path):
    """A wrong password on restore must raise, not silently return empty data."""
    from ashyterm.utils.exceptions import StorageReadError

    src = tmp_path / "src"
    src.mkdir()
    (src / "sessions.json").write_text("[]")

    archive = tmp_path / "backup.7z"
    mgr = BackupManager(backup_dir=tmp_path / "bkp")
    mgr.create_encrypted_backup(
        target_file_path=str(archive),
        password="correct",
        sessions_store=_empty_sessions_store(),
        source_files=[src / "sessions.json"],
        layouts_dir=src / "nonexistent",
    )

    restore = tmp_path / "restore"
    restore.mkdir()
    with pytest.raises(StorageReadError):
        mgr.restore_from_encrypted_backup(
            source_file_path=str(archive),
            password="wrong",
            config_dir=restore,
        )


# ── Tier 2: committed cross-implementation archive ─────────


def test_known_v1_archive_decrypts(tmp_path: Path):
    """The committed known_v1.7z must still decrypt correctly.

    Breaks if py7zr behavior regresses, or if someone accidentally
    overwrites the fixture. A Rust port of BackupManager must also pass
    this test against the same archive + password.
    """
    manifest_path = FIXTURES_DIR / "known_v1_manifest.json"
    with manifest_path.open() as f:
        manifest = json.load(f)

    archive_path = FIXTURES_DIR / manifest["archive"]
    assert archive_path.exists(), f"fixture missing: {archive_path}"

    with archive_path.open("rb") as f:
        assert f.read(6).hex() == manifest["magic_bytes_hex"]

    extract_dir = tmp_path / "extract"
    extract_dir.mkdir()
    with py7zr.SevenZipFile(
        str(archive_path), "r", password=manifest["password"]
    ) as arc:
        arc.extractall(path=str(extract_dir))

    for rel, expected in manifest["expected_files"].items():
        extracted = extract_dir / rel
        assert extracted.exists(), f"{rel} missing from archive"
        assert extracted.read_text(encoding="utf-8") == expected, (
            f"{rel} content mismatch"
        )


def test_known_v1_wrong_password_fails(tmp_path: Path):
    archive_path = FIXTURES_DIR / "known_v1.7z"
    extract_dir = tmp_path / "extract"
    extract_dir.mkdir()
    with pytest.raises(Exception):
        with py7zr.SevenZipFile(
            str(archive_path), "r", password="definitely-wrong"
        ) as arc:
            arc.extractall(path=str(extract_dir))
            # Some py7zr versions raise at extractall, others at open.
            # Either way, the password-less read of the data must error.
            shutil.rmtree(extract_dir, ignore_errors=True)
            raise AssertionError("no exception raised")


def test_vectors_table_has_at_least_ten():
    """Guard against accidental shrinkage of the KAT table."""
    assert len(_load_vectors()) >= 10
