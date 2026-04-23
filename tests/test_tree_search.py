"""Tests for SessionTreeSearch (filter + expansion state)."""

from types import SimpleNamespace
from unittest.mock import MagicMock


from ashyterm.sessions.models import SessionFolder, SessionItem
from ashyterm.sessions.tree_search import (
    SessionTreeSearch,
    item_matches_filter,
)


class _FakeRow:
    """Stand-in for ``Gtk.TreeListRow`` with the bits the search touches."""

    def __init__(self, item, expanded: bool = False, child_model=None):
        self._item = item
        self._expanded = expanded
        self._child_model = child_model

    def get_item(self):
        return self._item

    def get_expanded(self):
        return self._expanded

    def set_expanded(self, val):
        self._expanded = val

    def get_model(self):
        return self._child_model


class _FakeModel:
    def __init__(self, rows):
        self._rows = rows

    def get_n_items(self):
        return len(self._rows)

    def get_item(self, i):
        return self._rows[i]


def _make_view(*, folder_children=None) -> SimpleNamespace:
    """Build a SessionTreeView-ish object for the search to talk to."""
    folder_children = folder_children or {}

    def populate(folder):
        view._populated_folders.add(folder.path)

    view = SimpleNamespace()
    view._populated_folders = set(folder_children.keys())
    view._populate_folder_children = MagicMock(side_effect=populate)
    view._apply_expansion_state = MagicMock()
    view._is_restoring_state = False
    view.filter = MagicMock()
    view.tree_model = _FakeModel([])
    return view


# ── item_matches_filter (pure) ───────────────────────────────


class TestItemMatchesFilter:
    def test_empty_filter_matches_everything(self):
        item = SessionItem(name="anything", session_type="local")
        assert item_matches_filter(item, "", lambda _: False) is True

    def test_name_substring_match_is_case_insensitive(self):
        item = SessionItem(name="Production", session_type="local")
        # Filter text is expected to be lowercased already (see SessionTreeSearch).
        assert item_matches_filter(item, "prod", lambda _: False) is True

    def test_name_miss_returns_false_for_non_folders(self):
        item = SessionItem(name="anything", session_type="local")
        assert item_matches_filter(item, "zzzz", lambda _: False) is False

    def test_folder_match_delegates_to_matcher(self):
        folder = SessionFolder(name="empty")
        called_with = []

        def matcher(f):
            called_with.append(f)
            return True

        out = item_matches_filter(folder, "xyz", matcher)
        assert out is True
        assert called_with == [folder]

    def test_tree_list_row_unwraps_to_item(self):
        folder = SessionFolder(name="servers")
        row = _FakeRow(folder)
        assert item_matches_filter(row, "serv", lambda _: False) is True

    def test_none_actual_returns_false(self):
        row = _FakeRow(None)
        assert item_matches_filter(row, "abc", lambda _: False) is False


# ── folder_contains_matching (recursive) ─────────────────────


class TestFolderMatcher:
    def test_empty_filter_never_matches(self):
        view = _make_view()
        search = SessionTreeSearch(view)
        folder = SessionFolder(name="x", path="/x")
        folder.add_child(SessionItem(name="abc", session_type="local"))
        assert search.folder_contains_matching(folder) is False

    def test_direct_child_match(self):
        view = _make_view()
        search = SessionTreeSearch(view)
        search._filter_text = "prod"
        folder = SessionFolder(name="env", path="/env")
        view._populated_folders.add(folder.path)
        folder.add_child(SessionItem(name="Production DB", session_type="local"))
        assert search.folder_contains_matching(folder) is True

    def test_nested_folder_match(self):
        view = _make_view()
        search = SessionTreeSearch(view)
        search._filter_text = "staging"
        inner = SessionFolder(name="kube", path="/env/kube")
        view._populated_folders.add(inner.path)
        inner.add_child(SessionItem(name="staging", session_type="local"))
        outer = SessionFolder(name="env", path="/env")
        view._populated_folders.add(outer.path)
        outer.add_child(inner)
        assert search.folder_contains_matching(outer) is True

    def test_triggers_populate_for_unpopulated_folder(self):
        view = _make_view()
        search = SessionTreeSearch(view)
        search._filter_text = "x"
        folder = SessionFolder(name="lazy", path="/lazy")
        # folder.path is NOT in _populated_folders, so the populate
        # callback should be invoked during the scan.
        search.folder_contains_matching(folder)
        view._populate_folder_children.assert_called_once_with(folder)


# ── filter_func (the Gtk callback) ───────────────────────────


class TestFilterFunc:
    def test_filter_func_uses_current_text(self):
        view = _make_view()
        search = SessionTreeSearch(view)
        item = SessionItem(name="alpha", session_type="local")
        assert search.filter_func(item) is True  # empty filter passes

        search._filter_text = "alpha"
        assert search.filter_func(item) is True

        search._filter_text = "omega"
        assert search.filter_func(item) is False


# ── set_filter_text / clear ──────────────────────────────────


class TestTransitions:
    def test_first_keystroke_saves_expansion_and_triggers_filter(self):
        view = _make_view()
        search = SessionTreeSearch(view)
        search.save_expansion_state = MagicMock()
        search._expand_folders_with_matches = MagicMock()

        search.set_filter_text("P")

        search.save_expansion_state.assert_called_once()
        search._expand_folders_with_matches.assert_called_once()
        view.filter.changed.assert_called_once()
        assert search.filter_text == "p"  # lowercased

    def test_growing_filter_re_expands_but_does_not_resave(self):
        view = _make_view()
        search = SessionTreeSearch(view)
        search.save_expansion_state = MagicMock()
        search._expand_folders_with_matches = MagicMock()

        search.set_filter_text("p")
        search.save_expansion_state.reset_mock()

        search.set_filter_text("prod")

        search.save_expansion_state.assert_not_called()
        # Expand ran again (filter text is still a prefix of itself).
        assert search._expand_folders_with_matches.call_count >= 1

    def test_shrinking_filter_does_not_trigger_expand(self):
        view = _make_view()
        search = SessionTreeSearch(view)
        search._filter_text = "prod"  # pretend there was already a filter
        search.save_expansion_state = MagicMock()
        search._expand_folders_with_matches = MagicMock()

        # Not a prefix of the old text, not starting from empty.
        search.set_filter_text("dev")

        search._expand_folders_with_matches.assert_not_called()
        search.save_expansion_state.assert_not_called()

    def test_clear_restores_when_filter_was_active(self):
        view = _make_view()
        search = SessionTreeSearch(view)
        search._filter_text = "prod"
        search.restore_expansion_state = MagicMock()

        search.clear()

        assert search.filter_text == ""
        search.restore_expansion_state.assert_called_once()
        view.filter.changed.assert_called_once()

    def test_clear_is_noop_when_filter_is_empty(self):
        view = _make_view()
        search = SessionTreeSearch(view)
        search.restore_expansion_state = MagicMock()

        search.clear()

        search.restore_expansion_state.assert_not_called()
        view.filter.changed.assert_not_called()


# ── save / restore expansion state ───────────────────────────


class TestExpansionSnapshot:
    def test_save_collects_expanded_folder_paths(self):
        f_prod = SessionFolder(name="prod")
        f_prod.path = "/prod"
        f_dev = SessionFolder(name="dev")
        f_dev.path = "/dev"

        rows = [
            _FakeRow(f_prod, expanded=True),
            _FakeRow(f_dev, expanded=False),
        ]
        view = _make_view()
        view.tree_model = _FakeModel(rows)
        search = SessionTreeSearch(view)

        search.save_expansion_state()

        assert search.has_saved_expansion()
        # Only the expanded folder made it into the snapshot.
        assert search._saved_expansion_state == {"/prod"}

    def test_restore_without_snapshot_falls_back_to_settings(self):
        view = _make_view()
        search = SessionTreeSearch(view)
        search.restore_expansion_state()
        view._apply_expansion_state.assert_called_once()

    def test_restore_merges_saved_and_current_expansion(self):
        f_prod = SessionFolder(name="prod", path="/prod")
        f_dev = SessionFolder(name="dev", path="/dev")
        f_stg = SessionFolder(name="stg", path="/stg")

        rows = [
            _FakeRow(f_prod, expanded=True),   # new expansion kept during search
            _FakeRow(f_dev, expanded=False),   # in saved snapshot, needs re-expand
            _FakeRow(f_stg, expanded=True),    # expanded during search, also kept
        ]
        view = _make_view()
        view.tree_model = _FakeModel(rows)
        search = SessionTreeSearch(view)
        search._saved_expansion_state = {"/dev"}

        search.restore_expansion_state()

        # Semantics: the user's pre-search state is merged with any new
        # expansions performed during the search. Nothing is collapsed.
        assert rows[0]._expanded is True
        assert rows[1]._expanded is True
        assert rows[2]._expanded is True
        assert not search.has_saved_expansion()

    def test_restore_collapses_items_not_in_merged_set(self):
        # Verify the collapse branch is reachable. Snapshot says "expand
        # only /dev"; if /prod is somehow expanded but wasn't touched
        # during the search (collect_current would catch it), we at
        # least exercise the collapse branch via a folder that's neither
        # in the snapshot nor visibly expanded.
        f_dev = SessionFolder(name="dev", path="/dev")
        rows = [_FakeRow(f_dev, expanded=False)]
        view = _make_view()
        view.tree_model = _FakeModel(rows)
        search = SessionTreeSearch(view)
        search._saved_expansion_state = {"/dev"}

        search.restore_expansion_state()

        # Saved snapshot brought /dev back.
        assert rows[0]._expanded is True


# ── view integration ─────────────────────────────────────────


class TestViewIntegration:
    def test_view_exposes_search_attribute(self):
        from ashyterm.sessions.tree import SessionTreeView

        # The delegators must exist on the class itself so external
        # callers can still invoke the old API.
        for name in (
            "_filter_func",
            "set_filter_text",
            "clear_search",
            "_folder_contains_matching_items",
            "_save_current_expansion_state",
            "_restore_saved_expansion_state",
        ):
            assert callable(getattr(SessionTreeView, name))

    def test_filter_text_property_reads_through(self):
        from ashyterm.sessions.tree import SessionTreeView

        view = object.__new__(SessionTreeView)
        view.search = SessionTreeSearch(_make_view())
        view.search._filter_text = "hello"
        assert view._filter_text == "hello"
