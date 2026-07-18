"""Tests for the file-manager breadcrumb helpers."""

from unittest.mock import MagicMock

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk

from ashyterm.filemanager.breadcrumb import (
    compute_navigation_path,
    rebuild_breadcrumb,
)
from ashyterm.filemanager.models import FileItem


def _item(name: str) -> FileItem:
    line = f"-rw-r--r-- 1 u u 0 2024-01-01 12:00:00.000000000 +0000 {name}"
    out = FileItem.from_ls_line(line)
    assert out is not None
    return out


# ── compute_navigation_path ──────────────────────────────────


class TestComputeNavigationPath:
    def test_navigates_into_subdirectory(self):
        assert compute_navigation_path("/home/user", _item("docs")) == "/home/user/docs"

    def test_strips_trailing_slash_before_joining(self):
        assert (
            compute_navigation_path("/home/user/", _item("docs"))
            == "/home/user/docs"
        )

    def test_dot_dot_goes_to_parent(self):
        assert compute_navigation_path("/home/user/docs", _item("..")) == "/home/user"

    def test_dot_dot_at_root_returns_empty(self):
        # Caller treats empty as "do nothing" — we must not climb above /.
        assert compute_navigation_path("/", _item("..")) == ""

    def test_subfolder_with_trailing_slash_still_parents_cleanly(self):
        # Path.parent normalizes the trailing slash.
        assert compute_navigation_path("/home/user/", _item("..")) == "/home"

    def test_root_descent_does_not_double_slash(self):
        assert compute_navigation_path("/", _item("etc")) == "/etc"


# ── rebuild_breadcrumb ───────────────────────────────────────


def _collect_buttons(box: Gtk.Box) -> list[Gtk.Button]:
    out = []
    child = box.get_first_child()
    while child is not None:
        if isinstance(child, Gtk.Button):
            out.append(child)
        child = child.get_next_sibling()
    return out


class TestRebuildBreadcrumb:
    def test_root_shows_single_slash_button(self):
        box = Gtk.Box()
        on_clicked = MagicMock()

        rebuild_breadcrumb(box, "/", on_clicked=on_clicked)

        buttons = _collect_buttons(box)
        assert len(buttons) == 1
        assert buttons[0].get_label() == "/"

    def test_root_button_emits_slash_path(self):
        box = Gtk.Box()
        on_clicked = MagicMock()
        rebuild_breadcrumb(box, "/", on_clicked=on_clicked)

        _collect_buttons(box)[0].emit("clicked")
        on_clicked.assert_called_once()
        # The signal hands back (button, "/").
        assert on_clicked.call_args[0][1] == "/"

    def test_deep_path_shows_one_button_per_segment(self):
        box = Gtk.Box()
        rebuild_breadcrumb(box, "/home/user/docs", on_clicked=MagicMock())

        labels = [b.get_label() for b in _collect_buttons(box)]
        # ``/`` + "home" + "user" + "docs" = 4 buttons.
        assert labels == ["/", "home", "user", "docs"]

    def test_segment_buttons_carry_cumulative_path(self):
        box = Gtk.Box()
        on_clicked = MagicMock()
        rebuild_breadcrumb(box, "/home/user", on_clicked=on_clicked)

        buttons = _collect_buttons(box)
        # Click each button; the second arg to the handler is the path.
        for btn in buttons:
            btn.emit("clicked")

        paths = [call.args[1] for call in on_clicked.call_args_list]
        assert paths == ["/", "/home", "/home/user"]

    def test_rebuild_wipes_previous_children(self):
        box = Gtk.Box()
        rebuild_breadcrumb(box, "/home/user/docs", on_clicked=MagicMock())
        rebuild_breadcrumb(box, "/tmp", on_clicked=MagicMock())

        labels = [b.get_label() for b in _collect_buttons(box)]
        assert labels == ["/", "tmp"]

    def test_empty_current_path_treated_as_root(self):
        box = Gtk.Box()
        rebuild_breadcrumb(box, "", on_clicked=MagicMock())
        buttons = _collect_buttons(box)
        assert len(buttons) == 1
        assert buttons[0].get_label() == "/"


# ── manager delegation ──────────────────────────────────────


class TestManagerDelegation:
    def test_manager_delegators_exist(self):
        from ashyterm.filemanager.manager import FileManager

        for name in ("_update_breadcrumb", "_compute_navigation_path"):
            assert callable(getattr(FileManager, name))
