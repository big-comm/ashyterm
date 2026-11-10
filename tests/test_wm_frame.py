"""Tests for wm_frame (title bar drawn by the X11 window manager)."""

import pytest

from ashyterm.ui.window_ui import hide_title_buttons
from ashyterm.utils import wm_frame


class _FakeDisplay:
    def get_name(self) -> str:
        return ":1"


class X11Display(_FakeDisplay):
    """Named like GdkX11.X11Display, which is what wm_frame checks."""


class WaylandDisplay(_FakeDisplay):
    pass


class _FakeWindow:
    def __init__(self, display: _FakeDisplay) -> None:
        self._display = display

    def get_display(self) -> _FakeDisplay:
        return self._display


# ── has_titlebar ────────────────────────────────────────────


class TestHasTitlebar:
    @pytest.mark.parametrize(
        "extents",
        [None, (0, 0, 0, 0), (1, 1, 1, 1), (4, 4, 7, 4)],
    )
    def test_no_title_bar_for_missing_or_border_only_frames(self, extents):
        assert wm_frame.has_titlebar(extents) is False

    @pytest.mark.parametrize("extents", [(0, 0, 8, 0), (1, 1, 26, 9)])
    def test_title_bar_when_top_extent_is_taller_than_a_border(self, extents):
        assert wm_frame.has_titlebar(extents) is True


# ── wm_draws_titlebar ───────────────────────────────────────


class TestWmDrawsTitlebar:
    def test_wayland_is_never_inspected(self, monkeypatch):
        def fail(*args):
            raise AssertionError("must not read X11 properties on Wayland")

        monkeypatch.setattr(wm_frame, "read_frame_extents", fail)
        assert wm_frame.wm_draws_titlebar(_FakeWindow(WaylandDisplay())) is False

    def test_reads_the_frame_of_the_window(self, monkeypatch):
        calls = []
        monkeypatch.setattr(wm_frame, "_x11_xid", lambda window: 42)
        monkeypatch.setattr(
            wm_frame,
            "read_frame_extents",
            lambda xid, name: calls.append((xid, name)) or (1, 1, 26, 9),
        )
        assert wm_frame.wm_draws_titlebar(_FakeWindow(X11Display())) is True
        assert calls == [(42, ":1")]

    def test_errors_never_reach_the_window(self, monkeypatch):
        def broken(xid, name):
            raise OSError("display went away")

        monkeypatch.setattr(wm_frame, "_x11_xid", lambda window: 42)
        monkeypatch.setattr(wm_frame, "read_frame_extents", broken)
        assert wm_frame.wm_draws_titlebar(_FakeWindow(X11Display())) is False

    def test_no_libx11_means_no_frame(self, monkeypatch):
        monkeypatch.setattr(wm_frame, "_load_xlib", lambda: None)
        assert wm_frame.read_frame_extents(42, ":1") is None


# ── hide_title_buttons ──────────────────────────────────────


class TestHideTitleButtons:
    @pytest.mark.parametrize("setting", ["auto", "always"])
    def test_wm_title_bar_hides_the_headerbar_buttons(self, setting):
        assert hide_title_buttons(False, True, setting) is True

    def test_never_keeps_the_buttons_under_a_wm_title_bar(self):
        assert hide_title_buttons(False, True, "never") is False

    @pytest.mark.parametrize("hidden_when_maximized", [False, True])
    def test_without_wm_title_bar_the_maximized_rule_decides(
        self, hidden_when_maximized
    ):
        assert (
            hide_title_buttons(hidden_when_maximized, False, "auto")
            is hidden_when_maximized
        )
