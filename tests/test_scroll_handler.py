"""Contracts for custom-first terminal scrolling and GTK fallback."""

import weakref
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
import gi

gi.require_version("Gdk", "4.0")
from gi.repository import Gdk

import ashyterm.terminal.scroll_handler as scroll_module
from ashyterm.terminal.scroll_handler import ScrollHandler
from ashyterm.terminal.terminal_body import is_terminal_body


class FakeSettings:
    def __init__(self, **values):
        self.values = {"kinetic_scrolling": 0, **values}
        self.listeners = []

    def get(self, key, default=None):
        return self.values.get(key, default)

    def add_change_listener(self, listener):
        self.listeners.append(listener)


class FakeAdjustment:
    def __init__(
        self,
        value=50.0,
        lower=0.0,
        upper=200.0,
        page_size=20.0,
        step=10.0,
        ignore_writes=False,
    ):
        self.value = value
        self.lower = lower
        self.upper = upper
        self.page_size = page_size
        self.step = step
        self.ignore_writes = ignore_writes
        self.writes = []

    def get_value(self):
        return self.value

    def set_value(self, value):
        self.writes.append(value)
        if not self.ignore_writes:
            self.value = value

    def get_lower(self):
        return self.lower

    def get_upper(self):
        return self.upper

    def get_page_size(self):
        return self.page_size

    def get_step_increment(self):
        return self.step


class FakeScrolledWindow:
    def __init__(self, adjustment):
        self.adjustment = adjustment

    def get_vadjustment(self):
        return self.adjustment


class FakeController:
    def __init__(self, unit, source=Gdk.InputSource.MOUSE, control=False):
        self.unit = unit
        self.source = source
        self.control = control

    def get_unit(self):
        return self.unit

    def get_current_event_state(self):
        return Gdk.ModifierType.CONTROL_MASK if self.control else 0

    def get_current_event(self):
        device = SimpleNamespace(get_source=lambda: self.source)
        return SimpleNamespace(get_device=lambda: device)


class LegacyController:
    def get_current_event_state(self):
        return 0

    def get_current_event(self):
        device = SimpleNamespace(get_source=lambda: Gdk.InputSource.MOUSE)
        return SimpleNamespace(get_device=lambda: device)


@pytest.fixture
def scroll_handler():
    settings = FakeSettings()
    terminal_manager = SimpleNamespace(settings_manager=settings)
    tab_manager = SimpleNamespace(terminal_manager=terminal_manager)
    return ScrollHandler(tab_manager)


def run_scroll(handler, sw, controller, dy=1.0):
    return handler._on_terminal_scroll(controller, 0.0, dy, weakref.ref(sw))


def test_automatic_wheel_uses_step_and_mouse_sensitivity(scroll_handler):
    adjustment = FakeAdjustment()
    sw = FakeScrolledWindow(adjustment)
    controller = FakeController(Gdk.ScrollUnit.WHEEL)

    result = run_scroll(scroll_handler, sw, controller)

    assert result == Gdk.EVENT_STOP
    assert adjustment.value == 80.0


def test_surface_uses_pixels_with_recalibrated_touchpad_sensitivity(scroll_handler):
    scroll_handler.tm.terminal_manager.settings_manager.values.update(
        {"touchpad_scroll_sensitivity": 30.0}
    )
    adjustment = FakeAdjustment(step=100.0)
    sw = FakeScrolledWindow(adjustment)
    controller = FakeController(
        Gdk.ScrollUnit.SURFACE, source=Gdk.InputSource.TOUCHPAD
    )

    result = run_scroll(scroll_handler, sw, controller, dy=10.0)

    assert result == Gdk.EVENT_STOP
    assert adjustment.value == 62.0


def test_surface_sensitivity_fifty_matches_previous_hundred(scroll_handler):
    scroll_handler.tm.terminal_manager.settings_manager.values.update(
        {"touchpad_scroll_sensitivity": 50.0}
    )
    adjustment = FakeAdjustment(step=0.0)
    sw = FakeScrolledWindow(adjustment)
    controller = FakeController(
        Gdk.ScrollUnit.SURFACE, source=Gdk.InputSource.TOUCHPAD
    )

    result = run_scroll(scroll_handler, sw, controller, dy=10.0)

    assert result == Gdk.EVENT_STOP
    assert adjustment.value == 70.0


def test_legacy_gtk_without_scroll_unit_uses_step_increment(scroll_handler):
    adjustment = FakeAdjustment()
    sw = FakeScrolledWindow(adjustment)

    result = run_scroll(scroll_handler, sw, LegacyController())

    assert result == Gdk.EVENT_STOP
    assert adjustment.value == 80.0


def test_automatic_propagates_when_adjustment_rejects_write(scroll_handler):
    adjustment = FakeAdjustment(ignore_writes=True)
    sw = FakeScrolledWindow(adjustment)
    controller = FakeController(Gdk.ScrollUnit.WHEEL)

    result = run_scroll(scroll_handler, sw, controller)

    assert result == Gdk.EVENT_PROPAGATE
    assert adjustment.value == 50.0


def test_custom_mode_never_falls_through_to_native(scroll_handler):
    scroll_handler.tm.terminal_manager.settings_manager.values[
        "terminal_scroll_mode"
    ] = "custom"
    adjustment = FakeAdjustment(ignore_writes=True)
    sw = FakeScrolledWindow(adjustment)

    result = run_scroll(
        scroll_handler, sw, FakeController(Gdk.ScrollUnit.WHEEL)
    )

    assert result == Gdk.EVENT_STOP


def test_native_mode_does_not_touch_custom_adjustment(scroll_handler):
    scroll_handler.tm.terminal_manager.settings_manager.values[
        "terminal_scroll_mode"
    ] = "native"
    adjustment = FakeAdjustment()
    sw = FakeScrolledWindow(adjustment)

    result = run_scroll(
        scroll_handler, sw, FakeController(Gdk.ScrollUnit.WHEEL)
    )

    assert result == Gdk.EVENT_PROPAGATE
    assert adjustment.writes == []


@pytest.mark.parametrize("mode", ["automatic", "custom", "native"])
def test_control_wheel_keeps_zoom_in_every_mode(scroll_handler, monkeypatch, mode):
    settings = scroll_handler.tm.terminal_manager.settings_manager
    settings.values.update({"terminal_scroll_mode": mode, "font": "Monospace 12"})
    apply_font = MagicMock()
    scroll_handler.tm.terminal_manager._apply_font_size_change = apply_font
    idle_add = MagicMock()
    monkeypatch.setattr(
        scroll_module,
        "GLib",
        SimpleNamespace(idle_add=idle_add),
    )
    sw = FakeScrolledWindow(FakeAdjustment())

    result = run_scroll(
        scroll_handler,
        sw,
        FakeController(Gdk.ScrollUnit.WHEEL, control=True),
        dy=-1.0,
    )

    assert result == Gdk.EVENT_STOP
    idle_add.assert_called_once_with(apply_font, "Monospace", 13)


def test_control_surface_keeps_zooming_during_same_gesture(
    scroll_handler, monkeypatch
):
    settings = scroll_handler.tm.terminal_manager.settings_manager
    settings.values["font"] = "Monospace 12"
    apply_font = MagicMock()
    scroll_handler.tm.terminal_manager._apply_font_size_change = apply_font
    idle_add = MagicMock()
    monkeypatch.setattr(scroll_module, "GLib", SimpleNamespace(idle_add=idle_add))
    sw = FakeScrolledWindow(FakeAdjustment())
    controller = FakeController(Gdk.ScrollUnit.SURFACE, control=True)

    run_scroll(scroll_handler, sw, controller, dy=-1.0)
    run_scroll(scroll_handler, sw, controller, dy=-1.0)

    assert idle_add.call_count == 2


def test_boundary_is_handled_without_invoking_native(scroll_handler):
    adjustment = FakeAdjustment(value=180.0, upper=200.0, page_size=20.0)
    sw = FakeScrolledWindow(adjustment)

    result = run_scroll(
        scroll_handler, sw, FakeController(Gdk.ScrollUnit.WHEEL), dy=1.0
    )

    assert result == Gdk.EVENT_STOP
    assert adjustment.writes == []


@pytest.mark.parametrize("dy", [0.0, float("nan"), float("inf")])
def test_automatic_propagates_invalid_delta(scroll_handler, dy):
    sw = FakeScrolledWindow(FakeAdjustment())

    result = run_scroll(
        scroll_handler, sw, FakeController(Gdk.ScrollUnit.WHEEL), dy=dy
    )

    assert result == Gdk.EVENT_PROPAGATE


def test_binding_is_idempotent_and_preserves_scrolled_window(monkeypatch):
    settings = FakeSettings()
    tab_manager = SimpleNamespace(
        terminal_manager=SimpleNamespace(settings_manager=settings)
    )
    handler = ScrollHandler(tab_manager)
    controller = MagicMock()
    fake_gtk = SimpleNamespace(
        EventControllerScroll=SimpleNamespace(new=MagicMock(return_value=controller)),
        EventControllerScrollFlags=SimpleNamespace(VERTICAL=1),
        PropagationPhase=SimpleNamespace(CAPTURE=1),
    )
    monkeypatch.setattr(scroll_module, "Gtk", fake_gtk)
    host = MagicMock()
    host.connect.return_value = 7
    sw = FakeScrolledWindow(FakeAdjustment())

    handler.bind_scroll_controller(host, sw)
    handler.bind_scroll_controller(host, sw)

    host.add_controller.assert_called_once_with(controller)
    host.remove_controller.assert_not_called()
    assert not hasattr(sw, "remove_controller")


def test_rebind_detaches_only_custom_host_controller(monkeypatch):
    settings = FakeSettings()
    tab_manager = SimpleNamespace(
        terminal_manager=SimpleNamespace(settings_manager=settings)
    )
    handler = ScrollHandler(tab_manager)
    controllers = [MagicMock(), MagicMock()]
    fake_gtk = SimpleNamespace(
        EventControllerScroll=SimpleNamespace(new=MagicMock(side_effect=controllers)),
        EventControllerScrollFlags=SimpleNamespace(VERTICAL=1),
        PropagationPhase=SimpleNamespace(CAPTURE=1),
    )
    monkeypatch.setattr(scroll_module, "Gtk", fake_gtk)
    first_host = MagicMock()
    first_host.connect.return_value = 1
    second_host = MagicMock()
    second_host.connect.return_value = 2
    sw = FakeScrolledWindow(FakeAdjustment())

    handler.bind_scroll_controller(first_host, sw)
    handler.bind_scroll_controller(second_host, sw)

    first_host.remove_controller.assert_called_once_with(controllers[0])
    first_host.disconnect.assert_called_once_with(1)
    second_host.add_controller.assert_called_once_with(controllers[1])


def test_terminal_body_marker_does_not_match_back_reference():
    terminal_body = SimpleNamespace(_ashy_terminal_body=True)
    scrolled_window = SimpleNamespace(_ashy_terminal_body=terminal_body)

    assert is_terminal_body(terminal_body) is True
    assert is_terminal_body(scrolled_window) is False
