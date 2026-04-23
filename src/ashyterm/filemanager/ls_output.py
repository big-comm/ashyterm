# ashyterm/filemanager/ls_output.py
"""Pure helpers for parsing ``ls -la`` output and classifying its errors.

The file manager talks to the remote/local shell by running
``ls -la`` and parsing the resulting lines into :class:`FileItem`
objects. Parsing and error-classification are pure text operations
that don't touch GTK or the live model, so they live here and can be
exercised without mounting the file manager.
"""

from __future__ import annotations

from typing import Callable, List

from .models import FileItem


_CONNECTION_ERROR_TERMS = (
    "timed out",
    "timeout",
    "connection",
    "network",
    "unreachable",
)


def normalize_path_for_ls(path: str) -> str:
    """Ensure ``path`` ends with a trailing slash as ``ls`` expects."""
    return path if path.endswith("/") else f"{path}/"


def is_connection_error(output: str) -> bool:
    """Return True if ``output`` looks like a network/connection error.

    The heuristic scans the lowercased text for a handful of well-known
    terms. It's intentionally loose — we prefer a false positive (which
    just skips the fallback-to-last-successful-path) over silently
    treating a dead SSH pipe as a permission error.
    """
    lower = output.lower()
    return any(term in lower for term in _CONNECTION_ERROR_TERMS)


def should_fallback(
    *,
    is_connection_err: bool,
    requested_path: str,
    last_successful_path: str,
) -> bool:
    """Decide whether to fall back to the previous successful directory.

    We fall back only when the failure is *not* network-related (a
    connection drop should surface to the user) AND we actually have a
    different previous path to fall back to.
    """
    return bool(
        not is_connection_err
        and last_successful_path
        and last_successful_path != requested_path
    )


def resolve_link_target(file_item: FileItem, base_path: str) -> None:
    """Rewrite a relative symlink target to its absolute form, in place."""
    if file_item.is_link and file_item._link_target:
        if not file_item._link_target.startswith("/"):
            file_item._link_target = (
                f"{base_path.rstrip('/')}/{file_item._link_target}"
            )


def parse_ls_output(
    output: str,
    requested_path: str,
    *,
    should_abort: Callable[[], bool] = lambda: False,
) -> List[FileItem]:
    """Turn ``ls -la`` output into a sorted list of :class:`FileItem`.

    Rules:
    * The first line (``total N``) is discarded.
    * ``.`` is dropped. ``..`` is preserved only when the requested path
      is not the root, and it's always emitted first.
    * Directories precede files; each group is sorted case-insensitively.
    * ``should_abort()`` is consulted between lines so callers can cut
      parsing short when the user navigates away mid-load.
    """
    lines = output.strip().split("\n")[1:]
    directories: List[FileItem] = []
    files: List[FileItem] = []
    parent_item: FileItem | None = None

    for line in lines:
        if should_abort():
            return []

        file_item = FileItem.from_ls_line(line)
        if not file_item:
            continue

        if file_item.name == "..":
            parent_item = file_item
        elif file_item.name != ".":
            resolve_link_target(file_item, requested_path)
            if file_item.is_directory_like:
                directories.append(file_item)
            else:
                files.append(file_item)

    directories.sort(key=lambda x: x.name.lower())
    files.sort(key=lambda x: x.name.lower())

    all_items: List[FileItem] = []
    if requested_path != "/" and parent_item:
        all_items.append(parent_item)
    all_items.extend(directories)
    all_items.extend(files)
    return all_items
