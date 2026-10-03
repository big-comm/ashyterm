"""Regression coverage for touchpad inertia beyond the last physical event."""

from types import SimpleNamespace
from unittest.mock import MagicMock

import gi
import pytest

gi.require_version("Gdk", "4.0")
from gi.repository import Gdk

import ashyterm.terminal.scroll_handler as scroll_module
from ashyterm.terminal.application_scroll_momentum import (
    ApplicationScrollMomentum,
    scroll_report_direction,
)
from ashyterm.terminal.scroll_handler import ScrollHandler
from ashyterm.terminal.scroll_mode_guard import ScrollModeGuard

UP = "\x1b[<64;10;12M"
DOWN = "\x1b[<65;10;12M"


class Terminal:
    def __init__(self):
        self.mapped = True
        self.upper = 400
        self.sent = []

    def get_char_height(self):
        return 20

    def get_mapped(self):
        return self.mapped

    def get_vadjustment(self):
        return SimpleNamespace(get_lower=lambda: 0, get_upper=lambda: self.upper, get_page_size=lambda: 400)

    def feed_child(self, report):
        self.sent.append(report)


@pytest.fixture
def momentum(monkeypatch):
    clock = SimpleNamespace(now=1.0)
    glib = SimpleNamespace(
        timeout_add=MagicMock(side_effect=iter(range(1, 1000))),
        source_remove=MagicMock(), SOURCE_REMOVE=False, SOURCE_CONTINUE=True,
    )
    monkeypatch.setattr(scroll_module, "GLib", glib)
    monkeypatch.setattr(scroll_module, "time", SimpleNamespace(monotonic=lambda: clock.now))
    settings = {"kinetic_scrolling": 80}
    engine = ScrollHandler(SimpleNamespace(terminal_manager=SimpleNamespace(settings_manager=settings)))
    terminal = Terminal()
    target = ApplicationScrollMomentum(terminal, engine)
    target.track([UP, UP], Gdk.InputSource.TOUCHPAD)
    clock.now += 0.02
    target.track([UP, UP], Gdk.InputSource.TOUCHPAD)
    return SimpleNamespace(engine=engine, target=target, terminal=terminal, clock=clock, glib=glib)


def test_shared_engine_continues_after_release_and_decelerates_to_stop(momentum):
    audit = momentum
    audit.engine._start_kinetic_deceleration(audit.target)
    velocities = []
    for _ in range(500):
        audit.clock.now += 0.016
        velocities.append(abs(audit.target._k_vel))
        if not audit.engine._kinetic_tick(audit.target):
            break
    else:
        pytest.fail("Application inertia did not stop")

    assert len(audit.terminal.sent) > 2
    assert all(report.replace(UP.encode(), b"") == b"" for report in audit.terminal.sent)
    assert all(a >= b for a, b in zip(velocities, velocities[1:]))
    assert audit.target._k_anim is None


def test_sub_row_finger_motion_defers_kinetic_start(momentum):
    audit = momentum
    previous_idle = audit.target._k_idle
    audit.clock.now += 0.02

    audit.target.track([], Gdk.InputSource.TOUCHPAD)

    assert audit.target._k_idle != previous_idle
    assert audit.target._k_history[-1] == (audit.clock.now, 0.0)
    audit.glib.source_remove.assert_any_call(previous_idle)


def test_cancel_prevents_further_input(momentum):
    audit = momentum
    audit.engine._start_kinetic_deceleration(audit.target)
    audit.target.cancel()
    audit.clock.now += 0.02

    assert audit.engine._kinetic_tick(audit.target) is False
    assert audit.terminal.sent == []
    assert audit.target._k_history == []


@pytest.mark.parametrize("change", ["unmap", "history"])
def test_replay_stops_when_terminal_hides_or_leaves_alternate_screen(momentum, change):
    audit = momentum
    audit.engine._start_kinetic_deceleration(audit.target)
    if change == "unmap":
        audit.terminal.mapped = False
    else:
        audit.terminal.upper = 800
    audit.clock.now += 0.02

    assert audit.engine._kinetic_tick(audit.target) is False
    assert audit.terminal.sent == []


def test_direction_change_discards_previous_velocity(momentum):
    audit = momentum
    audit.clock.now += 0.02
    audit.target.track([DOWN], Gdk.InputSource.TOUCHPAD)

    assert audit.target._k_history == [(audit.clock.now, 20.0)]
    audit.target.set_value(audit.target.get_value() + 40)
    assert audit.terminal.sent == [DOWN.encode() * 2]


def test_replay_retains_fractional_rows(momentum):
    audit = momentum
    before = audit.target.get_value()
    audit.target.set_value(before - 10)
    assert audit.terminal.sent == []
    audit.target.set_value(before - 20)
    assert audit.terminal.sent == [UP.encode()]


@pytest.mark.parametrize("report,direction", [
    (UP, -1), (DOWN, 1), ("\x1b[<68;1;1M", -1),
    ("\x1b[A", -1), ("\x1bOB", 1), ("\x1b[1;2A", -1),
    ("\x1b[M`!!", -1), ("\x1b[Ma!!", 1),
    ("\x1b[<0;1;1M", 0), ("\x1b[<35;1;1M", 0),
    ("\x1b[<66;1;1M", 0), ("echo hello", 0),
])
def test_only_vertical_scroll_reports_can_be_replayed(report, direction):
    assert scroll_report_direction(report) == direction


@pytest.mark.parametrize("sequence", [
    b"\x1b[?1049l", b"\x1b[?1000;1002;1006l", b"\x1b[?1007h",
    b"\x1b[?1r", b"\x1bc", b"\x1b[!p", b"\x9b?1006l",
])
def test_mode_changes_are_detected_at_every_chunk_boundary(sequence):
    for offset in range(len(sequence) + 1):
        guard = ScrollModeGuard()
        first = guard.feed(sequence[:offset])
        second = guard.feed(sequence[offset:])
        assert first or second


@pytest.mark.parametrize("sequence", [
    b"\x1b[?25l\x1b[?2026htext\x1b[?2026l", b"\x1b[H\x1b[2J",
    b"\x1b]0;\x1b[?1049l\x07", b"\x1bP\x1b[?1000l\x1b\\",
])
def test_redraws_and_string_payloads_do_not_cancel_inertia(sequence):
    guard = ScrollModeGuard()
    assert not any(guard.feed(bytes([byte])) for byte in sequence)


def test_proxy_observes_modes_before_buffering_output():
    from ashyterm.terminal._highlighter_impl import HighlightedTerminalProxy

    guard = ScrollModeGuard()
    changes = []
    terminal = SimpleNamespace(_ashy_application_scroll=SimpleNamespace(
        observe_output=lambda chunk: changes.append(guard.feed(chunk))
    ))
    proxy = HighlightedTerminalProxy.__new__(HighlightedTerminalProxy)
    proxy._consume_osc52 = lambda chunk, _terminal: chunk
    proxy._combine_with_partial_buffer = lambda chunk: chunk
    proxy._has_incomplete_escape = lambda _chunk: True

    assert proxy._prepare_pty_data(b"\x1b[?104", terminal) is None
    assert proxy._prepare_pty_data(b"9l", terminal) is None
    assert changes == [False, True]
