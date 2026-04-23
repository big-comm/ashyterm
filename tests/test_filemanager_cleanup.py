"""Tests for filemanager.cleanup (teardown helpers)."""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from ashyterm.filemanager.cleanup import (
    cleanup_all,
    cleanup_data_stores,
    cleanup_edited_metadata,
    cleanup_file_monitors,
    cleanup_model_references,
    nullify_references,
)


def _fm(**overrides) -> SimpleNamespace:
    """Fake FileManager with the attributes cleanup reads.

    Every field that a real FM would publish during ``_build_ui`` is
    populated; overrides can remove or customize individual pieces
    to exercise the defensive branches.
    """
    defaults = dict(
        file_monitors={"/tmp/a": MagicMock(), "/tmp/b": MagicMock()},
        edited_file_metadata={"key1": "meta"},
        column_view=MagicMock(),
        selection_model=MagicMock(),
        sorted_store=MagicMock(),
        filtered_store=MagicMock(),
        store=MagicMock(),
        scrolled_window=MagicMock(),
        _parent_window_ref=MagicMock(),
        _terminal_manager_ref=MagicMock(),
        settings_manager=MagicMock(),
        operations=MagicMock(),
        transfer_manager=MagicMock(),
        main_box=MagicMock(),
        revealer=MagicMock(),
        bound_terminal=MagicMock(),
        session_item=MagicMock(),
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


# ── cleanup_file_monitors ──────────────────────────────────


class TestCleanupFileMonitors:
    def test_cancels_and_clears_each_monitor(self):
        fm = _fm()
        monitor_a = fm.file_monitors["/tmp/a"]
        monitor_b = fm.file_monitors["/tmp/b"]

        cleanup_file_monitors(fm)

        monitor_a.cancel.assert_called_once()
        monitor_b.cancel.assert_called_once()
        assert fm.file_monitors == {}

    def test_missing_attribute_is_safe(self):
        fm = SimpleNamespace()  # no file_monitors attr at all
        cleanup_file_monitors(fm)  # must not crash

    def test_empty_dict_is_safe(self):
        fm = _fm(file_monitors={})
        cleanup_file_monitors(fm)

    def test_none_values_are_skipped(self):
        monitor = MagicMock()
        fm = _fm(file_monitors={"a": None, "b": monitor})
        cleanup_file_monitors(fm)
        monitor.cancel.assert_called_once()


# ── cleanup_edited_metadata ────────────────────────────────


class TestCleanupEditedMetadata:
    def test_clears_metadata_dict(self):
        fm = _fm()
        cleanup_edited_metadata(fm)
        assert fm.edited_file_metadata == {}

    def test_missing_attribute_is_safe(self):
        fm = SimpleNamespace()
        cleanup_edited_metadata(fm)


# ── cleanup_model_references ───────────────────────────────


class TestCleanupModelReferences:
    def test_detaches_model_from_column_view(self):
        fm = _fm()
        cleanup_model_references(fm)
        fm.column_view.set_model.assert_called_once_with(None)

    def test_nulls_wrapper_references(self):
        fm = _fm()
        cleanup_model_references(fm)
        assert fm.selection_model is None
        assert fm.sorted_store is None
        assert fm.filtered_store is None

    def test_missing_column_view_is_safe(self):
        fm = _fm(column_view=None)
        cleanup_model_references(fm)


# ── cleanup_data_stores ────────────────────────────────────


class TestCleanupDataStores:
    def test_empties_store_and_drops_reference(self):
        fm = _fm()
        cleanup_data_stores(fm)
        assert fm.store is None

    def test_empties_scrolled_window(self):
        fm = _fm()
        cleanup_data_stores(fm)
        assert fm.scrolled_window is None

    def test_already_nulled_store_is_safe(self):
        fm = _fm(store=None, scrolled_window=None)
        cleanup_data_stores(fm)


# ── nullify_references ─────────────────────────────────────


class TestNullifyReferences:
    def test_every_back_reference_is_nulled(self):
        fm = _fm()
        nullify_references(fm)
        for attr in (
            "_parent_window_ref",
            "_terminal_manager_ref",
            "settings_manager",
            "operations",
            "transfer_manager",
            "column_view",
            "main_box",
            "revealer",
            "bound_terminal",
            "session_item",
        ):
            assert getattr(fm, attr) is None, f"{attr} should be None"


# ── cleanup_all pipeline ───────────────────────────────────


class TestCleanupAll:
    def test_runs_each_phase_in_order(self):
        fm = _fm()
        monitors = list(fm.file_monitors.values())

        cleanup_all(fm)

        # All phases must have landed their effects:
        for m in monitors:
            m.cancel.assert_called_once()
        assert fm.edited_file_metadata == {}
        assert fm.selection_model is None
        assert fm.store is None
        assert fm.settings_manager is None

    def test_idempotent_second_call_does_not_crash(self):
        fm = _fm()
        cleanup_all(fm)
        cleanup_all(fm)  # everything is already nulled; must be safe


# ── manager delegation ─────────────────────────────────────


class TestManagerDelegation:
    def test_manager_delegators_exist(self):
        from ashyterm.filemanager.manager import FileManager

        for name in (
            "_cleanup_file_monitors",
            "_cleanup_edited_metadata",
            "_cleanup_model_references",
            "_cleanup_data_stores",
            "_nullify_references",
        ):
            assert callable(getattr(FileManager, name))
