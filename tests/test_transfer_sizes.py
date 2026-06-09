"""Tests for transfer_sizes (pure size calculation for transfers)."""

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from ashyterm.filemanager.models import FileItem
from ashyterm.filemanager.transfer_sizes import (
    calculate_local_paths_size,
    calculate_remote_item_sizes,
)


def _ls_file(name: str, size: int) -> FileItem:
    """Synthesize a plain-file FileItem with the given size."""
    line = f"-rw-r--r-- 1 u u {size} 2024-01-01 12:00:00.000000000 +0000 {name}"
    item = FileItem.from_ls_line(line)
    assert item is not None
    return item


def _ls_dir(name: str) -> FileItem:
    line = f"drwxr-xr-x 1 u u 0 2024-01-01 12:00:00.000000000 +0000 {name}"
    item = FileItem.from_ls_line(line)
    assert item is not None
    return item


# ── calculate_remote_item_sizes ─────────────────────────────


class TestRemoteItemSizes:
    def test_plain_files_use_listed_size(self):
        ops = MagicMock()
        items = [_ls_file("a.txt", 100), _ls_file("b.bin", 2048)]

        out = calculate_remote_item_sizes(
            items, current_path="/remote", operations=ops, session_override=None
        )

        assert out == {"a.txt": 100, "b.bin": 2048}
        ops.get_directory_size.assert_not_called()

    def test_directories_use_listed_size_without_recursive_du(self):
        ops = MagicMock()
        ops.get_directory_size.return_value = 999_999
        items = [_ls_dir("src")]

        out = calculate_remote_item_sizes(
            items,
            current_path="/remote",
            operations=ops,
            session_override="session",
        )

        assert out == {"src": 0}
        ops.get_directory_size.assert_not_called()

    def test_zero_size_files_are_remeasured(self):
        """Listing sometimes reports size 0 for non-dir entries (e.g.
        special files); we defer to operations.get_directory_size."""
        ops = MagicMock()
        ops.get_directory_size.return_value = 42
        items = [_ls_file("sock", 0)]

        out = calculate_remote_item_sizes(
            items, current_path="/remote", operations=ops, session_override=None
        )

        assert out == {"sock": 42}

    def test_zero_size_when_operations_returns_zero_falls_back_to_listed(self):
        ops = MagicMock()
        ops.get_directory_size.return_value = 0
        items = [_ls_file("empty", 0)]

        out = calculate_remote_item_sizes(
            items, current_path="/remote", operations=ops, session_override=None
        )

        # Both sources said zero — the original (listed) wins so we
        # don't divide by zero later.
        assert out == {"empty": 0}

    def test_zero_size_item_trailing_slash_is_handled(self):
        ops = MagicMock()
        ops.get_directory_size.return_value = 1
        items = [_ls_file("sub", 0)]

        out = calculate_remote_item_sizes(
            items,
            current_path="/remote/",  # trailing slash must not duplicate
            operations=ops,
            session_override=None,
        )

        assert out == {"sub": 1}
        ops.get_directory_size.assert_called_once()
        # The first arg must be "/remote/sub", not "/remote//sub".
        remote_path = ops.get_directory_size.call_args[0][0]
        assert remote_path == "/remote/sub"

    def test_empty_list_returns_empty_dict(self):
        out = calculate_remote_item_sizes(
            [], current_path="/remote", operations=MagicMock(), session_override=None
        )
        assert out == {}


# ── calculate_local_paths_size ──────────────────────────────


class TestLocalPathsSize:
    def test_file_size_via_stat(self, tmp_path: Path):
        target = tmp_path / "file.bin"
        target.write_bytes(b"x" * 1234)
        ops = MagicMock()

        total, sizes = calculate_local_paths_size([target], operations=ops)

        assert sizes[str(target)] == 1234
        assert total == 1234
        # Pure files never hit operations.get_directory_size.
        ops.get_directory_size.assert_not_called()

    def test_directory_size_via_operations(self, tmp_path: Path):
        folder = tmp_path / "sub"
        folder.mkdir()
        ops = MagicMock()
        ops.get_directory_size.return_value = 4096

        total, sizes = calculate_local_paths_size([folder], operations=ops)

        assert sizes[str(folder)] == 4096
        assert total == 4096
        ops.get_directory_size.assert_called_once_with(
            str(folder), is_remote=False
        )

    def test_missing_files_contribute_zero(self, tmp_path: Path):
        missing = tmp_path / "gone.txt"
        real = tmp_path / "here.txt"
        real.write_bytes(b"hi")
        ops = MagicMock()

        total, sizes = calculate_local_paths_size(
            [missing, real], operations=ops
        )

        assert sizes[str(missing)] == 0
        assert sizes[str(real)] == 2
        assert total == 2

    def test_mix_of_files_and_dirs_sums_correctly(self, tmp_path: Path):
        f1 = tmp_path / "a.txt"
        f1.write_bytes(b"a" * 100)
        folder = tmp_path / "sub"
        folder.mkdir()
        ops = MagicMock()
        ops.get_directory_size.return_value = 1000

        total, sizes = calculate_local_paths_size([f1, folder], operations=ops)

        assert sizes[str(f1)] == 100
        assert sizes[str(folder)] == 1000
        assert total == 1100

    def test_empty_list_returns_zero_and_empty_dict(self):
        total, sizes = calculate_local_paths_size([], operations=MagicMock())
        assert total == 0
        assert sizes == {}


# ── mixin delegation ────────────────────────────────────────


class TestMixinDelegation:
    def test_mixin_still_exposes_size_methods(self):
        from ashyterm.filemanager.transfers import FileTransferMixin

        for name in ("_calculate_item_sizes", "_calculate_local_paths_size"):
            assert callable(getattr(FileTransferMixin, name))
