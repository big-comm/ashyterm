# ashyterm/sessions/tree_search.py
"""Filter + expansion-state logic for the sessions tree view.

The tree view's search behavior (incremental filter, auto-expand
folders containing matches, restore the user's original expansion
state on clear) is independent of the widget itself — it only needs
access to the filter model, the tree model, and the populator. Keeping
it here isolates a normally-flaky UI feature so the matching rules can
be exercised directly in tests.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Callable, Optional, Set

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk

from ..utils.logger import get_logger
from .models import SessionFolder

if TYPE_CHECKING:
    from .tree import SessionTreeView


def item_matches_filter(
    item,
    filter_text: str,
    folder_matcher: Callable[["SessionFolder"], bool],
) -> bool:
    """Return True if ``item`` should survive the given filter.

    ``filter_text`` is assumed already lowercased. Non-folder items
    match when their ``name`` contains the filter; folders delegate to
    ``folder_matcher`` so membership checks can recurse through the
    owning view's populator without duplicating that logic here.
    """
    if not filter_text:
        return True

    # Tree list rows expose get_item(); plain items do not.
    actual = item.get_item() if hasattr(item, "get_item") else item
    if actual is None:
        return False

    item_name = getattr(actual, "name", "").lower()
    if filter_text in item_name:
        return True

    if isinstance(actual, SessionFolder):
        return folder_matcher(actual)

    return False


class SessionTreeSearch:
    """Owns the filter state (text + saved expansion) for a tree view."""

    def __init__(self, view: "SessionTreeView") -> None:
        self.view = view
        self.logger = get_logger("ashyterm.sessions.tree_search")
        self._filter_text = ""
        self._saved_expansion_state: Optional[Set[str]] = None

    # ── read-only accessors ──────────────────────────────────

    @property
    def filter_text(self) -> str:
        return self._filter_text

    def has_saved_expansion(self) -> bool:
        return self._saved_expansion_state is not None

    # ── filter predicates ────────────────────────────────────

    def filter_func(self, item) -> bool:
        """Filter callback compatible with ``Gtk.CustomFilter.new``."""
        return item_matches_filter(
            item, self._filter_text, self.folder_contains_matching
        )

    def folder_contains_matching(self, folder: SessionFolder) -> bool:
        """Recursively check whether any descendant of ``folder`` matches."""
        if not self._filter_text:
            return False

        if folder.path not in self.view._populated_folders:
            self.view._populate_folder_children(folder)

        for child in folder.children:
            child_name = getattr(child, "name", "").lower()
            if self._filter_text in child_name:
                return True
            if isinstance(child, SessionFolder) and self.folder_contains_matching(
                child
            ):
                return True
        return False

    # ── transitions ──────────────────────────────────────────

    def set_filter_text(self, text: str) -> None:
        """Update the filter and schedule expansions/state saves as needed."""
        old_filter_text = self._filter_text
        self._filter_text = text.lower()

        # First keystroke after an empty filter ⇒ snapshot before expanding.
        if text and not old_filter_text:
            self.save_expansion_state()

        # Either the filter just started or the user kept typing ⇒ expand
        # folders whose descendants match so hits are visible immediately.
        if text and (not old_filter_text or text.startswith(old_filter_text)):
            self._expand_folders_with_matches()

        self.view.filter.changed(Gtk.FilterChange.DIFFERENT)

    def clear(self) -> None:
        """Clear the filter and restore the user's original expansion."""
        if self._filter_text:
            self._filter_text = ""
            self.restore_expansion_state()
            self.view.filter.changed(Gtk.FilterChange.DIFFERENT)

    # ── expansion snapshot ───────────────────────────────────

    def save_expansion_state(self) -> None:
        """Record every currently expanded folder path so we can restore later."""
        from .tree import iterate_tree_model  # avoid a circular import

        state: Set[str] = set()

        def collect(row, item, is_expanded):
            if isinstance(item, SessionFolder) and is_expanded:
                state.add(item.path)
            return is_expanded  # only recurse into expanded folders

        iterate_tree_model(self.view.tree_model, collect)
        self._saved_expansion_state = state
        self.logger.debug(f"Saved expansion state: {state}")

    def restore_expansion_state(self) -> None:
        """Restore the snapshot merged with anything the user expanded during search."""
        from .tree import iterate_tree_model

        if self._saved_expansion_state is None:
            self.view._apply_expansion_state()
            return

        current_expanded: Set[str] = set()

        def collect_current(row, item, is_expanded):
            if isinstance(item, SessionFolder) and is_expanded:
                current_expanded.add(item.path)
            return is_expanded

        iterate_tree_model(self.view.tree_model, collect_current)

        # User's pre-search state ∪ anything they expanded while searching.
        merged = self._saved_expansion_state | current_expanded
        self.logger.debug(f"Restoring merged expansion state: {merged}")

        def restore(row, item, is_expanded):
            if isinstance(item, SessionFolder):
                should_be_expanded = item.path in merged
                if should_be_expanded and not is_expanded:
                    row.set_expanded(True)
                elif not should_be_expanded and is_expanded:
                    row.set_expanded(False)
                return should_be_expanded
            return False

        self.view._is_restoring_state = True
        try:
            iterate_tree_model(self.view.tree_model, restore)
        finally:
            self.view._is_restoring_state = False

        self._saved_expansion_state = None

    # ── internal helpers ─────────────────────────────────────

    def _expand_folders_with_matches(self) -> None:
        if not self._filter_text:
            return
        self._expand_matching_folders_recursive(self.view.tree_model)

    def _expand_matching_folders_recursive(self, model) -> None:
        for i in range(model.get_n_items()):
            row = model.get_item(i)
            if not row:
                continue
            item = row.get_item()
            if not isinstance(item, SessionFolder):
                continue
            if not self.folder_contains_matching(item):
                continue
            self._expand_folder_if_needed(row, item)

    def _expand_folder_if_needed(self, row, item: SessionFolder) -> None:
        if not row.get_expanded():
            row.set_expanded(True)
        if item.path not in self.view._populated_folders:
            self.view._populate_folder_children(item)
