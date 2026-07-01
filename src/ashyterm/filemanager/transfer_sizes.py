# ashyterm/filemanager/transfer_sizes.py
"""Size-calculation helpers for the file-manager transfer flow.

Before kicking off a download or upload, the transfer mixin needs the
actual size of each item (for the progress bar and the free-space
check). For directories the size is obtained via ``operations`` (which
itself shells out to ``du``/SFTP), while plain files are read from the
listing or the local stat. The rules are the same for remote and
local, so they live here behind two small helpers.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Tuple

from .models import FileItem


def calculate_remote_item_sizes(
    items: List[FileItem],
    *,
    current_path: str,
    operations: Any,
    session_override: Any,
) -> Dict[str, int]:
    """Return ``{item.name: size_in_bytes}`` for each of ``items``.

    Directory sizes are resolved recursively because the free-space check
    and transfer progress must reflect what will actually be copied.
    Listing entries reporting ``0`` for non-directories are still resolved
    via ``operations.get_directory_size`` because remote ``ls`` sometimes
    omits a size for special files.
    """
    item_sizes: Dict[str, int] = {}
    base = current_path.rstrip("/")

    for item in items:
        remote_path = f"{base}/{item.name}"

        if item.is_directory_like:
            calculated = operations.get_directory_size(
                remote_path, is_remote=True, session_override=session_override
            )
            item_sizes[item.name] = calculated if calculated > 0 else item.size
        elif item.size == 0:
            calculated = operations.get_directory_size(
                remote_path, is_remote=True, session_override=session_override
            )
            item_sizes[item.name] = calculated if calculated > 0 else item.size
        else:
            item_sizes[item.name] = item.size

    return item_sizes


def calculate_local_paths_size(
    local_paths: List[Path],
    *,
    operations: Any,
) -> Tuple[int, Dict[str, int]]:
    """Return ``(total_bytes, {path: size})`` for the given local paths.

    Directories are walked through ``operations.get_directory_size`` so
    the computation matches what we do for remote sources. Regular
    files use ``Path.stat()``; non-existent paths contribute zero so a
    stale listing doesn't abort the whole upload batch.
    """
    total_bytes = 0
    path_sizes: Dict[str, int] = {}

    for local_path in local_paths:
        if local_path.is_dir():
            size = operations.get_directory_size(str(local_path), is_remote=False)
        else:
            size = local_path.stat().st_size if local_path.exists() else 0
        path_sizes[str(local_path)] = size
        total_bytes += size

    return total_bytes, path_sizes
