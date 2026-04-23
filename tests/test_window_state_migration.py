# tests/test_window_state_migration.py
"""Tests for the v1 → v2 session-state migration added by PR #46."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from ashyterm.state.window_state import _migrate_v1_to_v2


class TestMigrateV1ToV2:
    def test_adds_groups_key(self):
        data = {"tabs": []}
        result = _migrate_v1_to_v2(data)
        assert result["groups"] == []

    def test_preserves_existing_groups(self):
        data = {"tabs": [], "groups": [{"id": "g1"}]}
        result = _migrate_v1_to_v2(data)
        assert result["groups"] == [{"id": "g1"}]

    def test_fills_group_id_on_each_tab(self):
        data = {"tabs": [{"name": "t1"}, {"name": "t2", "group_id": "g1"}]}
        result = _migrate_v1_to_v2(data)
        assert result["tabs"][0] == {"name": "t1", "group_id": None}
        assert result["tabs"][1] == {"name": "t2", "group_id": "g1"}

    def test_noop_on_already_migrated(self):
        data = {"tabs": [{"name": "t1", "group_id": None}], "groups": []}
        result = _migrate_v1_to_v2(data)
        assert result == data


class TestMigrationsMapping:
    def test_migrations_bound_for_v1(self):
        from ashyterm.state.window_state import WindowStateManager

        assert 1 in WindowStateManager.MIGRATIONS
        assert WindowStateManager.SCHEMA_VERSION == 2

    def test_migration_callable_is_plain_function(self):
        """We intentionally avoided staticmethod.__func__ so this stays portable."""
        from ashyterm.state.window_state import WindowStateManager

        fn = WindowStateManager.MIGRATIONS[1]
        assert callable(fn)
        # Not a bound or descriptor — must be a plain function we can call.
        out = fn({"tabs": []})
        assert out["groups"] == []
