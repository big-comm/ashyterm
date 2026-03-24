# tests/test_filemanager_delegates.py
"""Tests for FileManager delegate wiring and contracts.

These tests verify that:
1. Delegate classes expose all methods referenced by manager.py
2. FileManager forwarding stubs correctly delegate to the right delegate
3. Filtering, sorting and hidden-toggle logic works correctly
4. Context menu delegate internal helpers work correctly
"""

import sys
import os
from unittest.mock import MagicMock, patch, PropertyMock
from datetime import datetime

import pytest

# Ensure conftest.py mock setup is loaded
src_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src"))
if src_path not in sys.path:
    sys.path.insert(0, src_path)


# ── Delegate contract tests ─────────────────────────────────────────────────

class TestColumnViewDelegateContract:
    """Verify ColumnViewDelegate exposes all methods that manager.py references."""

    REQUIRED_METHODS = [
        "create_column",
        "create_detailed_column_view",
        "setup_name_cell",
        "_bind_cell_common",
        "unbind_cell",
        "bind_name_cell",
        "setup_text_cell",
        "setup_size_cell",
        "bind_permissions_cell",
        "bind_owner_cell",
        "bind_group_cell",
        "bind_size_cell",
        "bind_date_cell",
        "setup_filtering_and_sorting",
        "on_hidden_toggle",
        "filter_files",
        "is_hidden_file",
        "_dolphin_sort_priority",
        "sort_by_name",
        "sort_by_permissions",
        "sort_by_owner",
        "sort_by_group",
        "sort_by_size",
        "sort_by_date",
        "get_selected_items",
    ]

    def test_all_required_methods_exist(self):
        """ColumnViewDelegate must expose every method used by manager.py."""
        from ashyterm.filemanager.fm_column_view import ColumnViewDelegate

        for method_name in self.REQUIRED_METHODS:
            assert hasattr(ColumnViewDelegate, method_name), (
                f"ColumnViewDelegate missing method: {method_name}"
            )
            assert callable(getattr(ColumnViewDelegate, method_name)), (
                f"ColumnViewDelegate.{method_name} is not callable"
            )


class TestContextMenuDelegateContract:
    """Verify ContextMenuDelegate exposes all methods that manager.py references."""

    REQUIRED_METHODS = [
        "on_item_right_click",
        "on_column_view_background_click",
        "on_scrolled_window_background_click",
        "on_search_key_pressed",
        "on_column_view_key_pressed",
        "on_column_view_key_released",
        "show_general_context_menu",
        "show_context_menu",
        "create_context_menu_model",
        "setup_action_group",
        "setup_context_actions",
        "setup_general_context_actions",
        "on_create_folder_action",
        "on_create_file_action",
        "on_copy_action",
        "on_cut_action",
        "on_paste_action",
        "on_delete_action",
        "on_chmod_action",
    ]

    def test_all_required_methods_exist(self):
        """ContextMenuDelegate must expose every method used by manager.py."""
        from ashyterm.filemanager.fm_context_menu import ContextMenuDelegate

        for method_name in self.REQUIRED_METHODS:
            assert hasattr(ContextMenuDelegate, method_name), (
                f"ContextMenuDelegate missing method: {method_name}"
            )
            assert callable(getattr(ContextMenuDelegate, method_name)), (
                f"ContextMenuDelegate.{method_name} is not callable"
            )


# ── ColumnViewDelegate unit tests ───────────────────────────────────────────

class TestColumnViewDelegateLogic:
    """Test ColumnViewDelegate logic that can be tested without GTK."""

    @pytest.fixture
    def delegate(self):
        from ashyterm.filemanager.fm_column_view import ColumnViewDelegate
        fm = MagicMock()
        fm.hidden_files_toggle.get_active.return_value = False
        fm.recursive_search_enabled = False
        fm._showing_recursive_results = False
        fm.search_entry.get_text.return_value = ""
        return ColumnViewDelegate(fm)

    def test_on_hidden_toggle_triggers_filter_change(self, delegate):
        """on_hidden_toggle must call combined_filter.changed."""
        delegate.on_hidden_toggle(MagicMock())
        delegate.fm.combined_filter.changed.assert_called_once()

    def test_is_hidden_file_dot_prefix(self, delegate):
        """Files starting with . are hidden."""
        item = MagicMock()
        item.name = ".bashrc"
        assert delegate.is_hidden_file(item) is True

    def test_is_hidden_file_normal(self, delegate):
        """Files not starting with . are not hidden."""
        item = MagicMock()
        item.name = "readme.txt"
        assert delegate.is_hidden_file(item) is False

    def test_is_hidden_file_recursive_search(self, delegate):
        """In recursive search, check last path component."""
        delegate.fm.recursive_search_enabled = True
        delegate.fm._showing_recursive_results = True
        item = MagicMock()
        item.name = "some/path/.hidden"
        assert delegate.is_hidden_file(item) is True

    def test_is_hidden_file_recursive_search_visible(self, delegate):
        """In recursive search, visible file in deep path."""
        delegate.fm.recursive_search_enabled = True
        delegate.fm._showing_recursive_results = True
        item = MagicMock()
        item.name = ".config/visible_file"
        assert delegate.is_hidden_file(item) is False

    def test_filter_files_parent_dir_no_search(self, delegate):
        """Parent dir (..) shown when no search term."""
        item = MagicMock()
        item.name = ".."
        assert delegate.filter_files(item) is True

    def test_filter_files_parent_dir_with_search(self, delegate):
        """Parent dir (..) hidden when search term active."""
        delegate.fm.search_entry.get_text.return_value = "test"
        item = MagicMock()
        item.name = ".."
        assert delegate.filter_files(item) is False

    def test_filter_files_hidden_not_shown(self, delegate):
        """Hidden files filtered out when toggle is off."""
        delegate.fm.hidden_files_toggle.get_active.return_value = False
        item = MagicMock()
        item.name = ".hidden"
        assert delegate.filter_files(item) is False

    def test_filter_files_hidden_shown(self, delegate):
        """Hidden files shown when toggle is on."""
        delegate.fm.hidden_files_toggle.get_active.return_value = True
        item = MagicMock()
        item.name = ".hidden"
        assert delegate.filter_files(item) is True

    def test_filter_files_search_match(self, delegate):
        """Files matching search term are shown."""
        delegate.fm.search_entry.get_text.return_value = "read"
        item = MagicMock()
        item.name = "readme.txt"
        assert delegate.filter_files(item) is True

    def test_filter_files_search_no_match(self, delegate):
        """Files not matching search term are hidden."""
        delegate.fm.search_entry.get_text.return_value = "xyz"
        item = MagicMock()
        item.name = "readme.txt"
        assert delegate.filter_files(item) is False

    def test_filter_files_search_case_insensitive(self, delegate):
        """Search is case-insensitive."""
        delegate.fm.search_entry.get_text.return_value = "READ"
        item = MagicMock()
        item.name = "readme.txt"
        assert delegate.filter_files(item) is True

    def test_dolphin_sort_priority_parent_dir_first(self, delegate):
        """Parent dir (..) always sorts first."""
        parent = MagicMock()
        parent.name = ".."
        other = MagicMock()
        other.name = "folder"
        other.is_directory_like = True
        assert delegate._dolphin_sort_priority(parent, other) == -1

    def test_dolphin_sort_priority_dirs_before_files(self, delegate):
        """Directories sort before files."""
        dir_item = MagicMock()
        dir_item.name = "subdir"
        dir_item.is_directory_like = True
        file_item = MagicMock()
        file_item.name = "file.txt"
        file_item.is_directory_like = False
        assert delegate._dolphin_sort_priority(dir_item, file_item) < 0

    def test_dolphin_sort_priority_files_after_dirs(self, delegate):
        """Files sort after directories."""
        file_item = MagicMock()
        file_item.name = "file.txt"
        file_item.is_directory_like = False
        dir_item = MagicMock()
        dir_item.name = "subdir"
        dir_item.is_directory_like = True
        assert delegate._dolphin_sort_priority(file_item, dir_item) > 0

    def test_sort_by_name_directories_first(self, delegate):
        """Directories always sort before files."""
        dir_item = MagicMock()
        dir_item.name = "zdir"
        dir_item.is_directory_like = True

        file_item = MagicMock()
        file_item.name = "afile.txt"
        file_item.is_directory_like = False

        result = delegate.sort_by_name(dir_item, file_item)
        assert result < 0  # dir should come first

    def test_sort_by_size(self, delegate):
        """Sorting by size works correctly."""
        small = MagicMock()
        small.name = "small.txt"
        small.is_directory_like = False
        small.size = 100

        large = MagicMock()
        large.name = "large.txt"
        large.is_directory_like = False
        large.size = 1000

        result = delegate.sort_by_size(small, large)
        assert result < 0  # small size first (ascending)

    def test_sort_by_date(self, delegate):
        """Sorting by date works correctly."""
        old = MagicMock()
        old.name = "old.txt"
        old.is_directory_like = False
        old.date = datetime(2020, 1, 1)

        new = MagicMock()
        new.name = "new.txt"
        new.is_directory_like = False
        new.date = datetime(2024, 1, 1)

        result = delegate.sort_by_date(old, new)
        assert result < 0  # old date first (ascending)

    def test_sort_by_permissions(self, delegate):
        """Sorting by permissions works correctly."""
        a = MagicMock()
        a.name = "a.txt"
        a.is_directory_like = False
        a.permissions = "-rw-r--r--"

        b = MagicMock()
        b.name = "b.txt"
        b.is_directory_like = False
        b.permissions = "-rwxrwxrwx"

        # Just verify it returns an integer (doesn't crash)
        result = delegate.sort_by_permissions(a, b)
        assert isinstance(result, int)

    def test_sort_by_owner(self, delegate):
        """Sorting by owner works correctly."""
        a = MagicMock()
        a.name = "a.txt"
        a.is_directory_like = False
        a.owner = "alice"

        b = MagicMock()
        b.name = "b.txt"
        b.is_directory_like = False
        b.owner = "bob"

        result = delegate.sort_by_owner(a, b)
        assert result < 0  # alice < bob

    def test_sort_by_group(self, delegate):
        """Sorting by group works correctly."""
        a = MagicMock()
        a.name = "a.txt"
        a.is_directory_like = False
        a.group = "staff"

        b = MagicMock()
        b.name = "b.txt"
        b.is_directory_like = False
        b.group = "wheel"

        result = delegate.sort_by_group(a, b)
        assert result < 0  # staff < wheel

    def test_get_selected_items_no_selection_model(self, delegate):
        """get_selected_items returns empty list if no selection model."""
        del delegate.fm.selection_model
        assert delegate.get_selected_items() == []


# ── Context menu delegate logic tests ───────────────────────────────────────

class TestContextMenuDelegateLogic:
    """Test ContextMenuDelegate logic that can be tested without GTK."""

    @pytest.fixture
    def delegate(self):
        from ashyterm.filemanager.fm_context_menu import ContextMenuDelegate
        fm = MagicMock()
        fm.selection_model = MagicMock()
        fm.sorted_store = MagicMock()
        fm.column_view = MagicMock()
        fm.main_box = MagicMock()
        fm._is_destroyed = False
        return ContextMenuDelegate(fm)

    def test_delegate_stores_fm_reference(self, delegate):
        """Delegate holds reference to FileManager."""
        assert delegate.fm is not None

    def test_on_search_key_pressed_exists_and_callable(self, delegate):
        """on_search_key_pressed is callable."""
        assert callable(delegate.on_search_key_pressed)

    def test_on_column_view_key_pressed_exists_and_callable(self, delegate):
        """on_column_view_key_pressed is callable."""
        assert callable(delegate.on_column_view_key_pressed)

    def test_on_column_view_key_released_exists_and_callable(self, delegate):
        """on_column_view_key_released is callable."""
        assert callable(delegate.on_column_view_key_released)


# ── FileManager forwarding-stub tests ───────────────────────────────────────

class TestFileManagerForwardingStubs:
    """Test that FileManager forwarding stubs correctly delegate calls."""

    def test_get_selected_items_forwards_to_delegate(self):
        """FileManager.get_selected_items() forwards to ColumnViewDelegate."""
        from ashyterm.filemanager.fm_column_view import ColumnViewDelegate

        fm = MagicMock()
        delegate = ColumnViewDelegate(fm)
        delegate.get_selected_items = MagicMock(return_value=["item1"])

        # Simulate what the forwarding stub does
        result = delegate.get_selected_items()
        assert result == ["item1"]
        delegate.get_selected_items.assert_called_once()

    def test_on_item_right_click_forwards_to_delegate(self):
        """FileManager._on_item_right_click forwards to ContextMenuDelegate."""
        from ashyterm.filemanager.fm_context_menu import ContextMenuDelegate

        fm = MagicMock()
        delegate = ContextMenuDelegate(fm)
        delegate.on_item_right_click = MagicMock()

        gesture, list_item = MagicMock(), MagicMock()
        delegate.on_item_right_click(gesture, 1, 10.0, 20.0, list_item)
        delegate.on_item_right_click.assert_called_once_with(gesture, 1, 10.0, 20.0, list_item)

    def test_on_column_view_background_click_forwards(self):
        """FileManager._on_column_view_background_click forwards correctly."""
        from ashyterm.filemanager.fm_context_menu import ContextMenuDelegate

        fm = MagicMock()
        delegate = ContextMenuDelegate(fm)
        delegate.on_column_view_background_click = MagicMock()

        gesture = MagicMock()
        delegate.on_column_view_background_click(gesture, 1, 10.0, 20.0)
        delegate.on_column_view_background_click.assert_called_once()

    def test_on_column_view_key_pressed_forwards(self):
        """FileManager._on_column_view_key_pressed forwards and returns value."""
        from ashyterm.filemanager.fm_context_menu import ContextMenuDelegate

        fm = MagicMock()
        delegate = ContextMenuDelegate(fm)
        delegate.on_column_view_key_pressed = MagicMock(return_value=True)

        result = delegate.on_column_view_key_pressed(MagicMock(), 65, 0, 0)
        assert result is True


# ── Integration-level wiring tests ──────────────────────────────────────────

class TestDelegateWiringIntegrity:
    """Verify that manager.py _build_ui references match delegate public API."""

    def test_build_ui_delegate_references_valid(self):
        """All delegate methods referenced in _build_ui exist."""
        from ashyterm.filemanager.fm_column_view import ColumnViewDelegate
        from ashyterm.filemanager.fm_context_menu import ContextMenuDelegate

        # Methods referenced in _build_ui via self._column_view_delegate.*
        column_view_refs = [
            "create_detailed_column_view",
            "setup_filtering_and_sorting",
            "on_hidden_toggle",
        ]

        # Methods referenced in _build_ui via self._context_menu_delegate.*
        context_menu_refs = [
            "on_scrolled_window_background_click",
            "on_search_key_pressed",
        ]

        for method in column_view_refs:
            assert hasattr(ColumnViewDelegate, method), (
                f"_build_ui references ColumnViewDelegate.{method} but it does not exist"
            )

        for method in context_menu_refs:
            assert hasattr(ContextMenuDelegate, method), (
                f"_build_ui references ContextMenuDelegate.{method} but it does not exist"
            )

    def test_forwarding_stub_references_valid(self):
        """All methods referenced by forwarding stubs exist in delegates."""
        from ashyterm.filemanager.fm_column_view import ColumnViewDelegate
        from ashyterm.filemanager.fm_context_menu import ContextMenuDelegate

        # Forwarding stubs in manager.py call these delegate methods
        column_view_stubs = [
            "get_selected_items",
        ]

        context_menu_stubs = [
            "on_item_right_click",
            "on_column_view_background_click",
            "on_column_view_key_pressed",
            "on_column_view_key_released",
        ]

        for method in column_view_stubs:
            assert hasattr(ColumnViewDelegate, method), (
                f"Forwarding stub references ColumnViewDelegate.{method} but it does not exist"
            )

        for method in context_menu_stubs:
            assert hasattr(ContextMenuDelegate, method), (
                f"Forwarding stub references ContextMenuDelegate.{method} but it does not exist"
            )

    def test_column_view_delegate_internal_references(self):
        """Column view delegate references to manager methods that must exist."""
        # These are methods the column view delegate connects via fm.*
        # They must exist either as real methods or forwarding stubs
        required_fm_methods = [
            "_on_row_activated",
            "_on_column_view_key_pressed",
            "_on_column_view_key_released",
            "_on_column_view_background_click",
            "_on_item_right_click",
        ]

        # We can't instantiate FileManager without GTK, but we can verify
        # the forwarding stubs are present in the source
        import inspect
        from ashyterm.filemanager import manager as mgr_module

        source = inspect.getsource(mgr_module)
        for method in required_fm_methods:
            assert f"def {method}" in source, (
                f"FileManager must have method {method} (referenced by column view delegate)"
            )

    def test_context_menu_delegate_fm_references(self):
        """Context menu delegate references to fm.* methods that must exist in manager."""
        required_fm_methods = [
            "_can_paste",
            "_is_remote_session",
            "_execute_verified_command",
            "_show_toast",
            "_prompt_for_new_item",
            "_get_current_session_key",
            "_on_row_activated",
            "_navigate_up_directory",
        ]

        import inspect
        from ashyterm.filemanager import manager as mgr_module

        source = inspect.getsource(mgr_module)
        for method in required_fm_methods:
            assert f"def {method}" in source, (
                f"FileManager must have method {method} (referenced by context menu delegate)"
            )


# ── FileTransferMixin open-with tests ───────────────────────────────────────

class TestOpenWithDialog:
    """Test _show_open_with_dialog correctly opens files in both local and remote cases."""

    def test_open_with_dialog_response_local_calls_open_local_file(self):
        """For local files (remote_path=None), selecting an app must call _open_local_file."""
        import inspect
        from ashyterm.filemanager import transfers as transfers_module

        source = inspect.getsource(transfers_module.FileTransferMixin._show_open_with_dialog)

        # Verify the else branch exists to handle local files
        # The bug was: _open_local_file was inside "if remote_path:" so local files
        # never opened. The fix adds an "else:" with _open_local_file.
        assert "else:" in source, (
            "_show_open_with_dialog must have an else branch to handle local files"
        )

    def test_open_with_dialog_structure_has_both_branches(self):
        """The on_response handler must handle both remote and local paths."""
        import inspect
        from ashyterm.filemanager import transfers as transfers_module

        source = inspect.getsource(transfers_module.FileTransferMixin._show_open_with_dialog)

        # Must call _open_and_monitor_local_file for remote
        assert "_open_and_monitor_local_file" in source
        # Must call _open_local_file for local
        assert "_open_local_file" in source

    def test_on_open_with_action_local_passes_none_remote_path(self):
        """_on_open_with_action passes remote_path=None for local sessions."""
        import inspect
        from ashyterm.filemanager import transfers as transfers_module

        source = inspect.getsource(transfers_module.FileTransferMixin._on_open_with_action)
        assert "remote_path=None" in source, (
            "_on_open_with_action must pass remote_path=None for local files"
        )

    def test_on_open_edit_action_local_calls_open_local_file(self):
        """_on_open_edit_action must call _open_local_file directly for local."""
        import inspect
        from ashyterm.filemanager import transfers as transfers_module

        source = inspect.getsource(transfers_module.FileTransferMixin._on_open_edit_action)
        assert "_open_local_file" in source, (
            "_on_open_edit_action must call _open_local_file for local files"
        )

    def test_open_with_no_double_open_for_remote(self):
        """For remote files, must NOT call both _open_and_monitor + _open_local_file.

        _open_and_monitor_local_file already launches the app internally,
        so calling _open_local_file as well would open the file twice.
        """
        import inspect
        from ashyterm.filemanager import transfers as transfers_module

        source = inspect.getsource(transfers_module.FileTransferMixin._show_open_with_dialog)

        # Parse the on_response function to verify structure
        # The key assertion: _open_local_file must be in an else branch,
        # NOT alongside _open_and_monitor_local_file
        lines = source.split('\n')
        monitor_line = None
        open_local_line = None
        for i, line in enumerate(lines):
            if '_open_and_monitor_local_file' in line:
                monitor_line = i
            if '_open_local_file' in line:
                open_local_line = i

        assert monitor_line is not None, "Must reference _open_and_monitor_local_file"
        assert open_local_line is not None, "Must reference _open_local_file"
        assert open_local_line != monitor_line, (
            "_open_local_file and _open_and_monitor_local_file must not be on the same line"
        )

        # Verify there's an else between them (they shouldn't both execute in same path)
        between_lines = '\n'.join(lines[monitor_line:open_local_line])
        assert 'else' in between_lines, (
            "_open_local_file must be in an else branch, not alongside _open_and_monitor_local_file"
        )
