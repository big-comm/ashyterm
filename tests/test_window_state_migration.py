# tests/test_window_state_migration.py
"""Tests for the session-state migrations (v1 → v2 groups, v2 → v3 windows)."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from ashyterm.state.session_autosave import _migrate_v1_to_v2, _migrate_v2_to_v3


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


class TestMigrateV2ToV3:
    def test_wraps_tabs_and_groups_into_one_window(self):
        data = {"tabs": [{"name": "t1"}], "groups": [{"id": "g1"}]}
        assert _migrate_v2_to_v3(data) == {
            "windows": [{"groups": [{"id": "g1"}], "tabs": [{"name": "t1"}]}]
        }

    def test_empty_state_has_no_windows(self):
        assert _migrate_v2_to_v3({"tabs": [], "groups": []}) == {"windows": []}


class TestMigrationsMapping:
    def test_migrations_bound_for_v1_and_v2(self):
        from ashyterm.state import session_autosave

        assert set(session_autosave.MIGRATIONS) == {1, 2}
        assert session_autosave.SCHEMA_VERSION == 3

    def test_migration_callable_is_plain_function(self):
        """We intentionally avoided staticmethod.__func__ so this stays portable."""
        from ashyterm.state import session_autosave

        fn = session_autosave.MIGRATIONS[1]
        assert callable(fn)
        # Not a bound or descriptor — must be a plain function we can call.
        out = fn({"tabs": []})
        assert out["groups"] == []
