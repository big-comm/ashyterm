"""ScrollModeGuard mirrors the mouse-tracking modes VTE applies from output."""

import time
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from ashyterm.terminal.scroll_mode_guard import (
    MOUSE_TRACKING_MODES,
    ScrollModeGuard,
    wheel_reaches_application,
)

TMUX_MOUSE_ON = b"\x1b[?1000h\x1b[?1002h\x1b[?1006h"
TMUX_MOUSE_OFF = b"\x1b[?1006l\x1b[?1000l\x1b[?1002l\x1b[?1003l"

# Expected active modes, verified against VTE 0.84 with DECRQM. The live test
# below re-checks every row against the installed VTE when a display exists.
VTE_CASES = [
    (TMUX_MOUSE_ON, {1000, 1002}),
    (TMUX_MOUSE_ON + TMUX_MOUSE_OFF, set()),
    (b"\x1b[?1006h", set()),  # SGR encoding alone does not track the mouse.
    (b"\x1b[?9h", {9}),
    (b"\x1b[?1001h", {1001}),
    (b"\x1b[?1003h", {1003}),
    (b"\x1b[?1002h\x1b[?1000l", {1002}),  # Modes are independent flags.
    (b"\x1b[?1000;1002;1006h", {1000, 1002}),
    (b"\x1b[?1000h\x1b[?1049h", {1000}),
    (b"\x1b[?1049h\x1b[?1000h\x1b[?1049l", {1000}),
    (b"\x1b[?1000h\x1b[!p", set()),  # DECSTR
    (b"\x1b[?1003h\x1bc", set()),  # RIS
    (b"\x1b[?1000h\x1b(c", {1000}),  # Charset designation, not RIS.
    (b"\x1b]0;[?1000h\x07", set()),
    (b"\x1bP[?1000h\x1b\\", set()),
    (b"\x1b_[?1000h\x1b\\", set()),
    (b"\x1b]0;x\x1b[?1000h\x07", {1000}),  # ESC aborts the OSC payload.
    (b"\x1bPtmux;\x1b\x1b[?1000h\x1b\\", {1000}),
    ("\u009b?1000h".encode(), {1000}),  # UTF-8 C1 CSI
    ("ě?1000h".encode(), set()),  # Raw 0x9B inside a UTF-8 letter.
    ("\x1b]0;x\u009b?1000h\x07".encode(), {1000}),
    (b"\x1b[?1000h\x1b[?1000s\x1b[?1000l\x1b[?1000r", {1000}),
    (b"\x1b[?1002h\x1b[?1002r", set()),  # Unsaved modes restore off.
    (b"\x1b[?1003h\x1b[?1003s\x1b[?1003l\x1b[?1003r\x1b[?1003l\x1b[?1003r", set()),
    (b"\x1b[?1002h\x1b[?1002s\x1bc\x1b[?1002r", set()),
    (b"\x1b[?1002h\x1b[?1002s\x1b[!p\x1b[?1002r", set()),
    (b"\x1b[?10\n00h", {1000}),  # C0 inside CSI is executed.
    (b"\x1b[?10\x1800h", set()),  # CAN
    (b"\x1b[?10\x1a00h", set()),  # SUB
    (b"\x1b[?10\xc3\xa900h", set()),  # Non-ASCII aborts the control.
    (b"\x1b[?1000$h", set()),
    (b"\x1b[?1000:1h", set()),
    (b"\x1b[1000h", set()),
    (b"\x1b[>1000h", set()),
    (b"\x1b[?;01000h", {1000}),
    (b"\x1b[?" + b"1;" * 31 + b"1000h", {1000}),
    (b"\x1b[?" + b"1;" * 32 + b"1000h", set()),  # VTE accepts 32 parameters.
]


def feed_split(data: bytes, offset: int) -> ScrollModeGuard:
    guard = ScrollModeGuard()
    guard.feed(data[:offset])
    guard.feed(data[offset:])
    return guard


@pytest.mark.parametrize("data,expected", VTE_CASES)
def test_guard_matches_vte_at_every_chunk_boundary(data, expected):
    for offset in range(len(data) + 1):
        assert feed_split(data, offset)._mouse_modes == expected


def test_guard_matches_installed_vte():
    gi = pytest.importorskip("gi")
    gi.require_version("Gtk", "4.0")
    gi.require_version("Vte", "3.91")
    from gi.repository import GLib, Gtk, Vte

    if not Gtk.init_check():
        pytest.skip("VTE needs a display")
    replies = [[] for _ in VTE_CASES]
    terminals = []
    for (data, _), received in zip(VTE_CASES, replies):
        terminal = Vte.Terminal()
        terminal.connect("commit", lambda _t, text, _n, r=received: r.append(text))
        query = b"".join(b"\x1b[?%d$p" % mode for mode in sorted(MOUSE_TRACKING_MODES))
        terminal.feed(data + query)
        terminals.append(terminal)
    # The raw method skips PyGObject's asyncio hook and its warnings.
    iterate = super(GLib.MainContext, GLib.MainContext.default()).iteration
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline and not all(len(r) == 5 for r in replies):
        iterate(False)

    for (data, expected), received in zip(VTE_CASES, replies):
        active = {
            mode for mode in MOUSE_TRACKING_MODES
            if f"\x1b[?{mode};1$y" in received
        }
        assert len(received) == 5, data
        assert active == expected, data


def test_tmux_toggle_reports_changes_and_restores_history():
    guard = ScrollModeGuard()

    assert guard.feed(TMUX_MOUSE_ON) is True
    assert guard.mouse_tracking
    assert guard.feed(b"\x1b[?25l\x1b[H redraw \x1b[?25h") is False
    assert guard.mouse_tracking
    assert guard.feed(TMUX_MOUSE_OFF) is True
    assert not guard.mouse_tracking


def test_save_does_not_report_a_change():
    guard = ScrollModeGuard()

    assert guard.feed(b"\x1b[?1000s") is False
    assert guard.feed(b"\x1b[?2026s") is False


def test_utf8_c1_lead_split_across_reads():
    guard = ScrollModeGuard()

    guard.feed(b"text\xc2")
    guard.feed(b"\x9b?1000h")

    assert guard.mouse_tracking


def test_oversized_control_is_dropped_and_stops_momentum():
    guard = ScrollModeGuard()

    assert guard.feed(b"\x1b[?" + b"0" * 300 + b"1000h") is True
    assert not guard.mouse_tracking
    assert guard.feed(b"\x1b[?1000h") is True
    assert guard.mouse_tracking


def test_terminal_owns_one_guard_across_scroll_bindings():
    terminal = SimpleNamespace()
    guard = ScrollModeGuard.for_terminal(terminal)
    guard.feed(TMUX_MOUSE_ON)

    assert ScrollModeGuard.for_terminal(terminal) is guard
    assert not ScrollModeGuard().observed
    assert guard.observed


def test_mock_attribute_is_not_mistaken_for_a_guard():
    terminal = MagicMock()

    assert isinstance(ScrollModeGuard.for_terminal(terminal), ScrollModeGuard)


def adjustment(upper: float) -> SimpleNamespace:
    return SimpleNamespace(
        get_lower=lambda: 0.0, get_upper=lambda: upper, get_page_size=lambda: 20.0
    )


@pytest.mark.parametrize("output,upper,expected", [
    (TMUX_MOUSE_ON, 2000.0, True),  # Regression: scrollback took tmux's wheel.
    (TMUX_MOUSE_ON, 20.0, True),
    (TMUX_MOUSE_ON + TMUX_MOUSE_OFF, 2000.0, False),
    (b"\x1b[?1006h", 2000.0, False),
    (b"", 20.0, True),  # Alternate screen: VTE sends cursor keys.
])
def test_wheel_reaches_application(output, upper, expected):
    terminal = SimpleNamespace()
    ScrollModeGuard.for_terminal(terminal).feed(output)

    assert wheel_reaches_application(terminal, adjustment(upper)) is expected


def test_unobserved_terminal_falls_back_to_history_check():
    assert wheel_reaches_application(None, adjustment(20.0)) is True
    assert wheel_reaches_application(None, adjustment(2000.0)) is False
