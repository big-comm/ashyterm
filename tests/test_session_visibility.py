"""Favorite visibility persists without deleting imported SSH hosts."""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from gi.repository import Gio

from ashyterm.sessions.models import SessionFolder, SessionItem
from ashyterm.sessions.operations import SessionOperations
from ashyterm.sessions.storage import SessionStorageManager
from ashyterm.sessions.tree import SessionTreeView
from ashyterm.sessions.tree_search import SessionTreeSearch, item_matches_filter
from ashyterm.utils.exceptions import StorageError


@pytest.fixture
def operations(tmp_path, monkeypatch):
    storage = SessionStorageManager()
    storage.sessions_file = tmp_path / "sessions.json"
    monkeypatch.setattr("ashyterm.sessions.operations.get_storage_manager", lambda: storage)
    return SessionOperations(Gio.ListStore.new(SessionItem), Gio.ListStore.new(SessionFolder), {})


def test_imported_host_stays_hidden_after_reload_and_reimport(operations, tmp_path):
    config = tmp_path / "ssh_config"
    original = "Host github-crazy\n  HostName github.com\n  User git\n"
    config.write_text(original)
    assert operations.import_sessions_from_ssh_config(config).success
    session = operations.session_store.get_item(0)

    assert operations.set_session_hidden(session, True).success
    saved, _ = operations.storage_manager.load_sessions_and_folders_safe()
    operations.session_store.remove_all()
    restored = SessionItem.from_dict(saved[0])
    operations.session_store.append(restored)
    operations.import_sessions_from_ssh_config(config)

    assert restored.hidden
    assert operations.session_store.get_n_items() == 1
    assert operations._ignored_ssh_config_hosts == set()
    assert config.read_text() == original
    assert operations.set_session_hidden(restored, False).success
    saved, _ = operations.storage_manager.load_sessions_and_folders_safe()
    assert saved[0]["hidden"] is False


@pytest.mark.parametrize("source", ["user", "ssh_config"])
def test_old_sessions_remain_visible(source):
    session = SessionItem.from_dict({"name": "server", "source": source})
    assert not session.hidden


@pytest.mark.parametrize("failure", [False, StorageError("disk unavailable")])
def test_failed_save_restores_visibility_and_metadata(operations, monkeypatch, failure):
    session = SessionItem("local", session_type="local")
    operations.session_store.append(session)
    before = session.to_dict()
    save = MagicMock(return_value=False)
    if isinstance(failure, Exception):
        save.side_effect = failure
    monkeypatch.setattr(operations, "_save_changes", save)
    signals = MagicMock()
    monkeypatch.setattr("ashyterm.sessions.operations.AppSignals.get", lambda: signals)

    assert not operations.set_session_hidden(session, True).success
    assert session.to_dict() == before
    signals.emit.assert_not_called()


def test_missing_session_is_not_saved(operations, monkeypatch):
    save = MagicMock()
    monkeypatch.setattr(operations, "_save_changes", save)
    assert not operations.set_session_hidden(SessionItem("missing"), True).success
    save.assert_not_called()


@pytest.mark.parametrize("query", ["", "git"])
def test_search_never_reveals_hidden_favorites(query):
    session = SessionItem("github", hidden=True)
    assert not item_matches_filter(session, query, lambda _folder: False)
    session.hidden = False
    assert item_matches_filter(session, query, lambda _folder: False)


def test_hidden_descendants_do_not_make_search_match_parent_folders():
    parent = SessionFolder("servers", path="servers")
    child = SessionFolder("work", path="servers/work", parent_path="servers")
    session = SessionItem("github", hidden=True)
    child.add_child(session)
    parent.add_child(child)
    view = SimpleNamespace(_populated_folders={parent.path, child.path})
    search = SessionTreeSearch(view)
    search._filter_text = "git"

    assert not search.folder_contains_matching(parent)
    session.hidden = False
    assert search.folder_contains_matching(parent)


def test_tree_updates_when_last_favorite_is_hidden_and_restored(operations):
    session = SessionItem("local", session_type="local")
    operations.session_store.append(session)
    view = SessionTreeView(
        SimpleNamespace(layouts=[]), operations.session_store,
        operations.folder_store, {}, operations,
    )
    try:
        assert view.filter_model.get_n_items() == 1
        assert operations.set_session_hidden(session, True).success
        assert view.filter_model.get_n_items() == 0
        assert operations.session_store.get_n_items() == 1
        assert operations.set_session_hidden(session, False).success
        assert view.filter_model.get_n_items() == 1
    finally:
        view.disconnect_signals()


def test_editing_hidden_session_preserves_visibility(operations):
    session = SessionItem("local", session_type="local", hidden=True)
    operations.session_store.append(session)
    updated = SessionItem("renamed", session_type="local")

    assert operations.update_session(0, updated).success
    assert session.name == "renamed"
    assert session.hidden
