"""Application scrolling preserves VTE dispatch and scales GTK input units."""

from types import SimpleNamespace
from unittest.mock import MagicMock

import gi
import pytest

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
from gi.repository import Gdk, Gio, Gtk

from ashyterm.terminal.application_scroll import ApplicationScrollHandler


class FakeTerminal:
    def __init__(self, controller):
        self.upper = 400.0
        self.controllers = Gio.ListStore.new(Gtk.EventController)
        self.controllers.append(controller)
        self.callbacks = {}
        self.reports = []
        self.focused = True

    def connect(self, signal, callback):
        handler_id = len(self.callbacks) + 1
        self.callbacks[handler_id] = (signal, callback)
        return handler_id

    def disconnect(self, handler_id):
        del self.callbacks[handler_id]

    def add_controller(self, controller):
        self.controllers.append(controller)

    def remove_controller(self, controller):
        found, index = self.controllers.find(controller)
        assert found
        self.controllers.remove(index)

    def has_focus(self):
        return self.focused

    def commit(self, text):
        for signal, callback in self.callbacks.values():
            if signal == "commit":
                callback(self, text, len(text))

    def observe_controllers(self):
        return self.controllers

    def get_vadjustment(self):
        return SimpleNamespace(
            get_lower=lambda: 0.0,
            get_upper=lambda: self.upper,
            get_page_size=lambda: 400.0,
        )

    def get_char_height(self):
        return 20


@pytest.fixture
def application_scroll():
    controller = Gtk.EventControllerScroll.new(Gtk.EventControllerScrollFlags.BOTH_AXES)
    controller.set_name("vte-scroll-controller")
    controller.get_unit = lambda: Gdk.ScrollUnit.SURFACE
    received = []

    def native_scroll(_controller, dx, dy):
        received.append((dx, dy))
        for report in terminal.reports:
            terminal.commit(report)
        return True

    controller.connect("scroll", native_scroll)
    terminal = FakeTerminal(controller)
    settings = {"terminal_scroll_mode": "custom", "touchpad_scroll_sensitivity": 50}
    handler = ApplicationScrollHandler.attach(terminal, settings)
    assert handler is not None
    yield SimpleNamespace(
        controller=controller, terminal=terminal, settings=settings,
        received=received, handler=handler,
    )
    handler.detach()


@pytest.mark.parametrize("mode", ["automatic", "custom"])
@pytest.mark.parametrize("dy", [-10.0, 10.0])
def test_touchpad_pixels_become_fractional_application_steps(application_scroll, mode, dy):
    audit = application_scroll
    audit.settings["terminal_scroll_mode"] = mode

    assert audit.controller.emit("scroll", 0.0, dy) is True
    assert audit.received == [(0.0, dy / 10.0)]


def test_live_touchpad_sensitivity_changes_application_speed(application_scroll):
    audit = application_scroll
    for sensitivity in (25, 50, 100):
        audit.settings["touchpad_scroll_sensitivity"] = sensitivity
        audit.controller.emit("scroll", 0.0, 5.0)

    assert audit.received == [(0.0, 0.25), (0.0, 0.5), (0.0, 1.0)]


def test_wheel_uses_mouse_sensitivity_without_pixel_conversion(application_scroll):
    audit = application_scroll
    audit.settings["mouse_scroll_sensitivity"] = 40
    audit.controller.get_unit = lambda: Gdk.ScrollUnit.WHEEL

    audit.controller.emit("scroll", 0.0, -1.0)

    assert audit.received == [(0.0, -4.0)]


def test_touchpad_with_wheel_units_uses_touchpad_setting(application_scroll):
    audit = application_scroll
    audit.controller.get_unit = lambda: Gdk.ScrollUnit.WHEEL
    audit.controller.get_current_event = lambda: SimpleNamespace(
        get_device=lambda: SimpleNamespace(get_source=lambda: Gdk.InputSource.TOUCHPAD)
    )

    audit.controller.emit("scroll", 0.0, -0.25)

    assert audit.received == [(0.0, -0.5)]


def test_native_mode_preserves_original_deltas(application_scroll):
    audit = application_scroll
    audit.settings["terminal_scroll_mode"] = "native"

    audit.controller.emit("scroll", 1.25, -10.0)

    assert audit.received == [(1.25, -10.0)]


def test_history_fallback_is_not_scaled_twice(application_scroll):
    audit = application_scroll
    audit.terminal.upper = 800.0

    audit.controller.emit("scroll", 0.0, -10.0)

    assert audit.received == [(0.0, -10.0)]


def test_horizontal_application_scroll_remains_unchanged(application_scroll):
    audit = application_scroll

    audit.controller.emit("scroll", 1.5, 10.0)

    assert audit.received == [(1.5, 1.0)]


@pytest.mark.parametrize("sensitivity", [0, -1, float("nan"), float("inf"), "bad"])
def test_invalid_sensitivity_preserves_native_input(application_scroll, sensitivity):
    audit = application_scroll
    audit.settings["touchpad_scroll_sensitivity"] = sensitivity

    audit.controller.emit("scroll", 0.0, 10.0)

    assert audit.received == [(0.0, 10.0)]


def test_detach_restores_native_handler_and_can_repeat(application_scroll):
    audit = application_scroll
    audit.controller.emit("scroll", 0.0, 10.0)
    audit.handler.detach()
    audit.handler.detach()
    assert audit.controller.emit("scroll", 0.0, 10.0) is True

    assert audit.received == [(0.0, 1.0), (0.0, 10.0)]


def test_native_unhandled_result_still_propagates():
    controller = Gtk.EventControllerScroll.new(Gtk.EventControllerScrollFlags.VERTICAL)
    controller.set_name("vte-scroll-controller")
    received = []
    controller.connect("scroll", lambda _c, dx, dy: received.append((dx, dy)) or False)
    terminal = FakeTerminal(controller)
    handler = ApplicationScrollHandler.attach(terminal, {"terminal_scroll_mode": "native"})
    try:
        assert controller.emit("scroll", 0.0, 1.0) is False
        assert received == [(0.0, 1.0)]
    finally:
        handler.detach()


def test_unknown_controller_remains_untouched():
    controller = Gtk.EventControllerScroll.new(Gtk.EventControllerScrollFlags.VERTICAL)
    received = []
    controller.connect("scroll", lambda _c, dx, dy: received.append((dx, dy)) or True)
    terminal = FakeTerminal(controller)

    assert ApplicationScrollHandler.attach(terminal, {}) is None
    assert controller.emit("scroll", 0.0, 1.0) is True
    assert received == [(0.0, 1.0)]


@pytest.fixture
def momentum_scroll(application_scroll):
    audit = application_scroll
    audit.engine = MagicMock()
    audit.handler._bind_momentum(audit.terminal, audit.engine)
    audit.terminal.reports = ["\x1b[<64;10;12M"]
    return audit


def test_native_reports_feed_shared_engine_only_with_output_observer(momentum_scroll):
    audit = momentum_scroll
    audit.controller.emit("scroll", 0.0, -10.0)
    audit.engine._track_kinetic_scroll.assert_not_called()

    audit.handler.observe_output(b"\x1b[?1006h")
    audit.controller.emit("scroll", 0.0, -10.0)

    audit.engine._track_kinetic_scroll.assert_called_once_with(
        audit.handler._momentum, Gdk.InputSource.TOUCHPAD, -20.0,
    )


@pytest.mark.parametrize("event", ["input", "focus", "unmap", "mode", "key", "click"])
def test_activity_cancels_captured_report(momentum_scroll, event):
    audit = momentum_scroll
    audit.handler.observe_output(b"ready")
    audit.controller.emit("scroll", 0.0, -10.0)
    assert audit.handler._momentum._report
    if event == "input":
        audit.terminal.commit("x")
    elif event == "mode":
        audit.handler.observe_output(b"\x1b[?1006l")
    elif event == "focus":
        audit.terminal.focused = False
        audit.handler._on_focus_changed(audit.terminal, None)
    elif event == "unmap":
        for signal, callback in audit.terminal.callbacks.values():
            if signal == "unmap":
                callback(audit.terminal)
    elif event == "key":
        assert not audit.handler._on_key_pressed()
    else:
        gesture = MagicMock()
        audit.handler._on_pressed(gesture)
        gesture.set_state.assert_called_once_with(Gtk.EventSequenceState.DENIED)
    assert audit.handler._momentum._report == b""
    audit.engine._cancel_kinetic_scroll.assert_called_with(audit.handler._momentum)


def test_replayed_commit_does_not_cancel_own_momentum(momentum_scroll):
    audit = momentum_scroll
    audit.handler.observe_output(b"ready")
    audit.controller.emit("scroll", 0.0, -10.0)
    audit.handler._momentum.replaying = True

    audit.terminal.commit(audit.terminal.reports[0])

    assert audit.handler._momentum._report


def test_detach_removes_momentum_observers(momentum_scroll):
    audit = momentum_scroll
    audit.handler.detach()

    assert not audit.terminal.callbacks
    assert audit.terminal.controllers.get_n_items() == 1
    assert not hasattr(audit.terminal, "_ashy_application_scroll")
