"""Tests for ls_output helpers (pure text parsing for the file manager)."""

import pytest

from ashyterm.filemanager.ls_output import (
    is_connection_error,
    normalize_path_for_ls,
    parse_ls_output,
    resolve_link_target,
    should_fallback,
)
from ashyterm.filemanager.models import FileItem


# ── normalize_path_for_ls ────────────────────────────────────


class TestNormalizePath:
    def test_adds_trailing_slash(self):
        assert normalize_path_for_ls("/home/user") == "/home/user/"

    def test_keeps_existing_trailing_slash(self):
        assert normalize_path_for_ls("/home/") == "/home/"

    def test_root_is_already_terminated(self):
        assert normalize_path_for_ls("/") == "/"


# ── is_connection_error ──────────────────────────────────────


class TestIsConnectionError:
    @pytest.mark.parametrize(
        "text",
        [
            "ssh: connect to host example.com port 22: Connection refused",
            "ssh: Network is unreachable",
            "Connection timed out",
            "read from socket failed: connection reset by peer",
        ],
    )
    def test_detects_known_network_phrases(self, text):
        assert is_connection_error(text) is True

    def test_permission_denied_is_not_network(self):
        assert is_connection_error("ls: cannot access '/root': Permission denied") is False

    def test_empty_output_is_not_network(self):
        assert is_connection_error("") is False

    def test_case_insensitive(self):
        assert is_connection_error("TIMEOUT while connecting") is True


# ── should_fallback ──────────────────────────────────────────


class TestShouldFallback:
    def test_fallback_when_permission_denied_elsewhere(self):
        assert (
            should_fallback(
                is_connection_err=False,
                requested_path="/root",
                last_successful_path="/home/user",
            )
            is True
        )

    def test_no_fallback_on_connection_error(self):
        assert (
            should_fallback(
                is_connection_err=True,
                requested_path="/root",
                last_successful_path="/home/user",
            )
            is False
        )

    def test_no_fallback_when_no_last_path(self):
        assert (
            should_fallback(
                is_connection_err=False,
                requested_path="/root",
                last_successful_path="",
            )
            is False
        )

    def test_no_fallback_when_same_path(self):
        assert (
            should_fallback(
                is_connection_err=False,
                requested_path="/home/user",
                last_successful_path="/home/user",
            )
            is False
        )


# ── resolve_link_target ──────────────────────────────────────


class TestResolveLinkTarget:
    def _make_link(self, target: str) -> FileItem:
        """Synthesize a FileItem from a canned ls line pointing at ``target``."""
        line = f"lrwxrwxrwx 1 user user 4 2024-01-01 12:00:00.000000000 +0000 link -> {target}"
        item = FileItem.from_ls_line(line)
        assert item is not None
        return item

    def test_absolute_target_is_left_alone(self):
        item = self._make_link("/absolute/elsewhere")
        resolve_link_target(item, "/home/user/")
        assert item._link_target == "/absolute/elsewhere"

    def test_relative_target_is_prefixed_with_base(self):
        item = self._make_link("sibling")
        resolve_link_target(item, "/home/user/")
        assert item._link_target == "/home/user/sibling"

    def test_base_trailing_slash_is_normalized(self):
        item = self._make_link("sibling")
        resolve_link_target(item, "/home/user")
        assert item._link_target == "/home/user/sibling"

    def test_non_link_is_unchanged(self):
        line = "-rw-r--r-- 1 user user 4 2024-01-01 12:00:00.000000000 +0000 file.txt"
        item = FileItem.from_ls_line(line)
        assert item is not None
        resolve_link_target(item, "/home/user/")
        # Nothing crashes; no target was set.
        assert not getattr(item, "_link_target", None)


# ── parse_ls_output ──────────────────────────────────────────


def _ls_line(
    name: str,
    *,
    perms: str = "-rw-r--r--",
    size: int = 0,
) -> str:
    return (
        f"{perms} 1 user user {size} "
        f"2024-01-01 12:00:00.000000000 +0000 {name}"
    )


def _dir_line(name: str) -> str:
    return _ls_line(name, perms="drwxr-xr-x")


class TestParseLsOutput:
    def test_empty_output(self):
        assert parse_ls_output("total 0", "/home/user/") == []
        assert parse_ls_output("", "/home/user/") == []

    def test_total_line_is_dropped(self):
        raw = "\n".join(["total 4", _ls_line("a.txt")])
        items = parse_ls_output(raw, "/home/user/")
        assert len(items) == 1
        assert items[0].name == "a.txt"

    def test_current_directory_entry_is_dropped(self):
        raw = "\n".join(["total 0", _dir_line(".")])
        items = parse_ls_output(raw, "/home/user/")
        assert items == []

    def test_parent_dir_preserved_unless_at_root(self):
        raw = "\n".join(
            ["total 4", _dir_line(".."), _ls_line("a.txt"), _dir_line("sub")]
        )
        items = parse_ls_output(raw, "/home/user/")
        # .. must be first, directories before files, each sorted.
        assert items[0].name == ".."
        assert items[1].name == "sub"
        assert items[2].name == "a.txt"

    def test_parent_dir_dropped_at_root(self):
        raw = "\n".join(["total 4", _dir_line(".."), _ls_line("etc")])
        items = parse_ls_output(raw, "/")
        assert [i.name for i in items] == ["etc"]

    def test_directories_precede_files(self):
        raw = "\n".join(
            ["total 0", _ls_line("zeta.txt"), _dir_line("alpha")]
        )
        items = parse_ls_output(raw, "/home/user/")
        assert [i.name for i in items] == ["alpha", "zeta.txt"]

    def test_sort_is_case_insensitive(self):
        raw = "\n".join(
            ["total 0", _ls_line("Banana"), _ls_line("apple"), _ls_line("cherry")]
        )
        items = parse_ls_output(raw, "/home/user/")
        assert [i.name for i in items] == ["apple", "Banana", "cherry"]

    def test_malformed_lines_are_skipped(self):
        raw = "\n".join(
            ["total 0", "garbage", _ls_line("good.txt"), ""]
        )
        items = parse_ls_output(raw, "/home/user/")
        assert [i.name for i in items] == ["good.txt"]

    def test_abort_short_circuits_mid_parse(self):
        raw = "\n".join(
            ["total 0", _ls_line("a.txt"), _ls_line("b.txt"), _ls_line("c.txt")]
        )
        calls = {"n": 0}

        def should_abort():
            calls["n"] += 1
            # Abort after consuming the first line.
            return calls["n"] >= 2

        items = parse_ls_output(raw, "/home/user/", should_abort=should_abort)
        # When aborted mid-parse the helper returns [] so the caller
        # doesn't commit a half-populated listing to the UI.
        assert items == []


# ── manager integration (delegators exist) ──────────────────


class TestManagerDelegation:
    def test_manager_delegators_exist(self):
        from ashyterm.filemanager.manager import FileManager

        for name in (
            "_normalize_path_for_ls",
            "_is_connection_error",
            "_should_fallback",
            "_parse_ls_output",
            "_resolve_link_target",
        ):
            assert callable(getattr(FileManager, name))
