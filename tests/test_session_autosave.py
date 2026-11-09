"""Session snapshot: written continuously, kept on crash, cleared on purpose."""

import json
from types import SimpleNamespace

import pytest

from ashyterm.state import session_autosave
from ashyterm.state.session_autosave import (
    SessionAutosaver,
    close_keeps_session,
    load_saved_windows,
)


def window_with(*tab_ids, closing=False):
    tabs = [{"type": "terminal", "session_name": tab_id} for tab_id in tab_ids]
    state = {"groups": [], "tabs": tabs} if tabs else None
    return SimpleNamespace(
        state_manager=SimpleNamespace(serialize_window=lambda: state),
        _cleanup_performed=closing,
    )


class Settings:
    def __init__(self, enabled=True):
        self.values = {session_autosave.AUTOSAVE_SETTING: enabled}

    def get(self, key, default=None):
        return self.values.get(key, default)


@pytest.fixture
def state_file(tmp_path):
    return tmp_path / "session_state.json"


def make_autosaver(state_file, windows, enabled=True):
    return SessionAutosaver(Settings(enabled), state_file, lambda: windows)


def saved_tab_names(state_file):
    state = json.loads(state_file.read_text())
    assert state["_version"] == session_autosave.SCHEMA_VERSION
    return [[tab["session_name"] for tab in w["tabs"]] for w in state["windows"]]


class TestSave:
    def test_writes_every_open_window(self, state_file):
        make_autosaver(state_file, [window_with("a", "b"), window_with("c")]).save()
        assert saved_tab_names(state_file) == [["a", "b"], ["c"]]

    def test_skips_closing_and_empty_windows(self, state_file):
        windows = [window_with("a"), window_with("b", closing=True), window_with()]
        make_autosaver(state_file, windows).save()
        assert saved_tab_names(state_file) == [["a"]]

    def test_does_not_rewrite_an_unchanged_session(self, state_file, monkeypatch):
        writes = []
        real_write = session_autosave.atomic_json_write
        monkeypatch.setattr(
            session_autosave,
            "atomic_json_write",
            lambda path, data: writes.append(path) or real_write(path, data),
        )
        autosaver = make_autosaver(state_file, [window_with("a")])
        autosaver.save()
        autosaver.save()
        assert len(writes) == 1

    def test_no_tabs_never_erases_the_snapshot(self, state_file):
        make_autosaver(state_file, [window_with("a")]).save()
        make_autosaver(state_file, [window_with()]).save()
        assert saved_tab_names(state_file) == [["a"]]

    def test_disabled_writes_only_when_forced(self, state_file):
        autosaver = make_autosaver(state_file, [window_with("a")], enabled=False)
        autosaver.save()
        assert not state_file.exists()
        autosaver.save(force=True)
        assert saved_tab_names(state_file) == [["a"]]


class TestRestore:
    def test_first_window_claims_the_session_once(self, state_file):
        make_autosaver(state_file, [window_with("a"), window_with("b")]).save()
        autosaver = make_autosaver(state_file, [])
        assert len(autosaver.claim_restore()) == 2
        assert autosaver.claim_restore() is None

    def test_nothing_saved_still_counts_as_claimed(self, state_file):
        autosaver = make_autosaver(state_file, [])
        assert autosaver.claim_restore() == []
        assert autosaver.claim_restore() is None

    def test_waits_for_reopened_windows_before_writing(self, state_file):
        make_autosaver(state_file, [window_with("a"), window_with("b")]).save()
        windows = [window_with("a")]
        autosaver = make_autosaver(state_file, windows)
        autosaver.claim_restore()
        autosaver.save()  # window "b" is still being recreated
        assert saved_tab_names(state_file) == [["a"], ["b"]]
        windows.append(window_with("b"))
        autosaver.window_restored()
        autosaver.save()
        assert saved_tab_names(state_file) == [["a"], ["b"]]

    def test_finish_without_autosave_keeps_the_old_one_shot_file(self, state_file):
        make_autosaver(state_file, [window_with("a")]).save()
        autosaver = make_autosaver(state_file, [], enabled=False)
        autosaver.claim_restore()
        autosaver.finish_restore()
        assert not state_file.exists()

    def test_finish_with_autosave_keeps_the_snapshot(self, state_file):
        make_autosaver(state_file, [window_with("a")]).save()
        autosaver = make_autosaver(state_file, [window_with("a")])
        autosaver.claim_restore()
        autosaver.finish_restore()
        try:
            assert state_file.exists()
        finally:
            autosaver.freeze()


class TestClose:
    def test_keeping_saves_then_ignores_teardown(self, state_file):
        windows = [window_with("a", "b")]
        autosaver = make_autosaver(state_file, windows)
        autosaver.close_last_window(keep=True)
        windows[0] = window_with("a")  # tabs closing during teardown
        autosaver.save()
        assert saved_tab_names(state_file) == [["a", "b"]]

    def test_forgetting_removes_the_snapshot(self, state_file):
        autosaver = make_autosaver(state_file, [window_with("a")])
        autosaver.save()
        autosaver.close_last_window(keep=False)
        autosaver.save()
        assert not state_file.exists()

    def test_closing_one_window_drops_it_from_the_session(self, state_file):
        closed = window_with("b")
        windows = [window_with("a"), closed]
        autosaver = make_autosaver(state_file, windows)
        autosaver.save()
        windows.remove(closed)
        autosaver.on_window_removed(closed)
        assert saved_tab_names(state_file) == [["a"]]

    def test_last_window_gone_without_choice_forgets_the_session(self, state_file):
        closed = window_with("a")
        windows = [closed]
        autosaver = make_autosaver(state_file, windows)
        autosaver.save()
        windows.clear()  # every shell exited
        autosaver.on_window_removed(closed)
        assert not state_file.exists()

    def test_shutdown_freeze_keeps_the_snapshot(self, state_file):
        closed = window_with("a")
        windows = [closed]
        autosaver = make_autosaver(state_file, windows)
        autosaver.save()
        autosaver.freeze()  # quit action, logout or SIGTERM
        windows.clear()
        autosaver.on_window_removed(closed)
        assert saved_tab_names(state_file) == [["a"]]

    def test_non_terminal_windows_are_ignored(self, state_file):
        autosaver = make_autosaver(state_file, [])
        autosaver.save(force=True)
        state_file.write_text('{"_version": 3, "windows": [{"tabs": [{}]}]}')
        autosaver.on_window_removed(SimpleNamespace())  # e.g. a dialog
        assert state_file.exists()


@pytest.mark.parametrize(
    "policy, tab_count, response, keep",
    [
        ("ask", 3, "save", True),
        ("ask", 3, "close", False),
        ("save_and_close", 3, None, True),
        ("save_and_close", 1, None, False),
        ("just_close", 3, None, False),
        ("ask", 1, None, False),
    ],
)
def test_close_keeps_session(policy, tab_count, response, keep):
    assert close_keeps_session(policy, tab_count, response) is keep


class TestLoad:
    def test_missing_or_broken_file_restores_nothing(self, state_file):
        assert load_saved_windows(state_file) == []
        state_file.write_text("{not json")
        assert load_saved_windows(state_file) == []

    def test_single_window_file_from_older_versions(self, state_file):
        state_file.write_text(
            json.dumps({"_version": 2, "groups": [], "tabs": [{"id": "t"}]})
        )
        assert load_saved_windows(state_file) == [
            {"groups": [], "tabs": [{"id": "t"}]}
        ]
