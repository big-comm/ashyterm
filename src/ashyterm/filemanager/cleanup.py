# ashyterm/filemanager/cleanup.py
"""Teardown helpers for :class:`FileManager`.

``FileManager.destroy()`` has to detach from live file monitors,
drop Gtk model references, clear the backing stores, and null out
the Python-side fields so the instance can be garbage-collected.
Each step is defensive — it tolerates partial initialization
(attributes not present, already-None values) because ``destroy``
can fire at any point in the lifecycle, including before the UI has
finished building.

The helpers take the file-manager as a parameter; :class:`FileManager`
still exposes ``_cleanup_*`` delegator methods so call sites like
``destroy()`` don't change.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .manager import FileManager


def cleanup_file_monitors(fm: "FileManager") -> None:
    """Cancel and clear every ``Gio.FileMonitor`` the FM was holding.

    Tolerant to the attribute not existing or being empty — we want
    ``destroy()`` to work for FM instances that never finished
    building their UI (e.g. dialog error during ``_build_ui``).
    """
    monitors = getattr(fm, "file_monitors", None)
    if not monitors:
        return
    for monitor in monitors.values():
        if monitor:
            monitor.cancel()
    monitors.clear()


def cleanup_edited_metadata(fm: "FileManager") -> None:
    """Drop the edited-file metadata dict so the temp-file flow resets."""
    if hasattr(fm, "edited_file_metadata"):
        fm.edited_file_metadata.clear()


def cleanup_model_references(fm: "FileManager") -> None:
    """Detach the GTK model from the column view and drop wrappers.

    Without detaching, a live ``SelectionModel`` keeps pointing at
    the sorted/filtered wrappers which keep pointing at the backing
    store, which keeps the FM alive via signal handlers.
    """
    if getattr(fm, "column_view", None):
        fm.column_view.set_model(None)

    # Zero out the model wrappers so the next ``destroy()`` is idempotent.
    if hasattr(fm, "selection_model"):
        fm.selection_model = None
    if hasattr(fm, "sorted_store"):
        fm.sorted_store = None
    if hasattr(fm, "filtered_store"):
        fm.filtered_store = None


def cleanup_data_stores(fm: "FileManager") -> None:
    """Empty + release the backing ``Gio.ListStore`` and its container."""
    store = getattr(fm, "store", None)
    if store:
        store.remove_all()
        fm.store = None  # type: ignore[assignment]

    if getattr(fm, "scrolled_window", None):
        fm.scrolled_window = None  # type: ignore[assignment]


def nullify_references(fm: "FileManager") -> None:
    """Null out every Python-side reference that participates in a cycle.

    The window / terminal manager hold strong references to the FM via
    callbacks; nulling the back-pointers here lets the cycle collector
    pick up the whole graph.
    """
    fm._parent_window_ref = None  # type: ignore[assignment]
    fm._terminal_manager_ref = None  # type: ignore[assignment]
    fm.settings_manager = None
    fm.operations = None
    fm.transfer_manager = None  # type: ignore[assignment]
    fm.column_view = None
    fm.main_box = None
    fm.revealer = None
    fm.bound_terminal = None
    fm.session_item = None


def cleanup_all(fm: "FileManager") -> None:
    """Run the whole cleanup pipeline in order.

    The caller (``FileManager.destroy``) already ran ``shutdown``; this
    sequence is the "break reference cycles" phase that happens after
    network/filesystem state has already been torn down.
    """
    cleanup_file_monitors(fm)
    cleanup_edited_metadata(fm)
    cleanup_model_references(fm)
    cleanup_data_stores(fm)
    nullify_references(fm)
