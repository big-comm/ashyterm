"""Contract + fixture tests for ashyterm.utils.json_versioning.

Three layers:

1. Core framework properties of ``migrate_data``/``stamp_version``
   (no-op at current, walks missing migrations, refuses to downgrade).
2. Per-module migration fixtures under ``fixtures/json_migrations/<module>/``.
   Each ``*.before.json`` is paired with ``*.after.json``; the test
   loads the pair, looks up the real ``SCHEMA_VERSION`` + ``MIGRATIONS``
   table, runs the migration, and asserts equality.
3. Idempotence: running the migration twice (once, then again on the
   result) does not mutate further.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable, Dict

import pytest

from ashyterm.utils.json_versioning import migrate_data, stamp_version


FIXTURES_DIR = Path(__file__).parent / "fixtures" / "json_migrations"


# ── Framework-level tests ───────────────────────────────────


class TestMigrateDataCore:
    def test_at_current_version_is_identity(self):
        data = {"_version": 3, "x": 1}
        assert migrate_data(data, 3, {}) is data

    def test_newer_than_code_is_left_alone(self):
        """If persisted data has a version newer than what the code knows
        (e.g. user downgraded), pass through untouched. Corrupting a
        newer-format file would be worse than leaving it unreadable."""
        data = {"_version": 99, "x": 1}
        result = migrate_data(data, 3, {})
        assert result == data

    def test_walks_each_version_applying_migration(self):
        calls: list[int] = []

        def m1(d):
            calls.append(1)
            d["added_at_v1"] = True
            return d

        def m2(d):
            calls.append(2)
            d["added_at_v2"] = True
            return d

        data: Dict[str, Any] = {"_version": 0}
        result = migrate_data(data, 3, {0: m1, 1: m2})
        # m1 applied from 0→1, m2 from 1→2, then stamp-only 2→3.
        assert calls == [1, 2]
        assert result["_version"] == 3
        assert result["added_at_v1"] is True
        assert result["added_at_v2"] is True

    def test_missing_migration_still_bumps_version(self):
        """A gap in ``migrations`` is not an error — data stamp bumps
        through it. This lets callers skip writing trivial no-op
        migrations when nothing changed about the schema."""
        data: Dict[str, Any] = {"_version": 0, "x": 1}
        result = migrate_data(data, 2, {})
        assert result["_version"] == 2
        assert result["x"] == 1

    def test_missing_version_defaults_to_zero(self):
        """Legacy files (no _version field) are treated as v0."""
        data: Dict[str, Any] = {"x": 1}
        called = []

        def m0(d):
            called.append("m0")
            return d

        result = migrate_data(data, 1, {0: m0})
        assert called == ["m0"]
        assert result["_version"] == 1


class TestStampVersion:
    def test_sets_missing_version(self):
        d: Dict[str, Any] = {"x": 1}
        assert stamp_version(d, 4)["_version"] == 4

    def test_overwrites_existing_version(self):
        d: Dict[str, Any] = {"_version": 2}
        assert stamp_version(d, 5)["_version"] == 5

    def test_returns_same_dict(self):
        """Mutates in place and returns for chaining."""
        d: Dict[str, Any] = {"x": 1}
        assert stamp_version(d, 1) is d


# ── Fixture-driven module-specific migrations ───────────────


def _load_module_migrations(module_name: str):
    """Look up ``SCHEMA_VERSION`` + ``MIGRATIONS`` for a known module."""
    if module_name == "window_state":
        from ashyterm.state.window_state import WindowStateManager
        return WindowStateManager.SCHEMA_VERSION, WindowStateManager.MIGRATIONS
    raise ValueError(f"Unknown module: {module_name}")


def _discover_pairs():
    """Walk fixture dirs yielding (module_name, before_path, after_path)."""
    pairs = []
    if not FIXTURES_DIR.is_dir():
        return pairs
    for module_dir in FIXTURES_DIR.iterdir():
        if not module_dir.is_dir():
            continue
        for before in sorted(module_dir.glob("*.before.json")):
            stem = before.name.removesuffix(".before.json")
            after = module_dir / f"{stem}.after.json"
            if after.exists():
                pairs.append((module_dir.name, before, after))
    return pairs


@pytest.mark.parametrize(
    "module_name,before_path,after_path",
    _discover_pairs(),
    ids=lambda v: v.name if isinstance(v, Path) else v,
)
def test_migration_fixture(module_name: str, before_path: Path, after_path: Path):
    with before_path.open() as f:
        before = json.load(f)
    with after_path.open() as f:
        expected_after = json.load(f)

    target_version, migrations = _load_module_migrations(module_name)
    actual = migrate_data(before, target_version, migrations)
    assert actual == expected_after, (
        f"\nBefore:   {before_path.name}\n"
        f"Expected: {after_path.name}\n"
        f"Got:      {actual}"
    )


@pytest.mark.parametrize(
    "module_name,before_path,after_path",
    _discover_pairs(),
    ids=lambda v: v.name if isinstance(v, Path) else v,
)
def test_migration_is_idempotent(
    module_name: str, before_path: Path, after_path: Path
):
    """Running migrate_data on already-migrated data is a no-op. Catches
    migrations that re-apply their own changes (e.g. append instead of
    setdefault)."""
    with before_path.open() as f:
        before = json.load(f)

    target_version, migrations = _load_module_migrations(module_name)
    once = migrate_data(before, target_version, migrations)
    twice = migrate_data(once, target_version, migrations)
    assert once == twice


def test_fixture_directory_has_entries():
    assert len(_discover_pairs()) >= 4
