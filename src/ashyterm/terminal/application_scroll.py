"""Scale application scroll input while preserving VTE's mouse protocol."""

import math
import weakref
from typing import Any, Optional

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Vte", "3.91")
from gi.repository import Gdk, GObject, Gtk, Vte

from ..settings.scrolling import SCROLL_MODE_NATIVE, normalize_scroll_mode
from ..utils.logger import get_logger
from .application_scroll_momentum import ApplicationScrollMomentum
from .scroll_mode_guard import ScrollModeGuard

_logger = get_logger("ashyterm.terminal.application_scroll")


class ApplicationScrollHandler:
    """Normalize deltas before VTE encodes mouse reports or cursor keys.

    VTE has no public scroll multiplier. Its GTK scroll signal is wrapped
    without replacing the controller, synthesizing events, or parsing private
    terminal modes. Re-emission keeps the original event and pointer position.
    """

    def __init__(
        self,
        terminal: Vte.Terminal,
        controller: Gtk.EventControllerScroll,
        native_handler_id: int,
        settings: Any,
        scroll_engine: Any = None,
    ) -> None:
        self._terminal_ref = weakref.ref(terminal)
        self._controller_ref = weakref.ref(controller)
        self._native_handler_id = native_handler_id
        self._settings = settings
        self._handler_id = controller.connect("scroll", self._on_scroll)
        controller.handler_block(native_handler_id)
        self._momentum = None
        self._reports = None
        self._output_observed = False
        self._mode_guard = ScrollModeGuard()
        self._terminal_handlers = []
        self._input_controllers = []
        if scroll_engine is not None:
            self._bind_momentum(terminal, scroll_engine)

    @classmethod
    def attach(
        cls, terminal: Vte.Terminal, settings: Any, scroll_engine: Any = None
    ) -> Optional["ApplicationScrollHandler"]:
        """Wrap the identified VTE handler; leave unknown versions untouched."""
        controllers = terminal.observe_controllers()
        for index in range(controllers.get_n_items()):
            controller = controllers.get_item(index)
            if not isinstance(controller, Gtk.EventControllerScroll):
                continue
            if controller.get_name() != "vte-scroll-controller":
                continue
            signal_id = GObject.signal_lookup("scroll", controller.__gtype__)
            handler_id = GObject.signal_handler_find(
                controller, GObject.SignalMatchType.ID, signal_id, 0,
                None, None, None,
            )
            if handler_id:
                return cls(terminal, controller, handler_id, settings, scroll_engine)
        _logger.warning("VTE scroll handler unavailable; keeping native application scrolling")
        return None

    def detach(self) -> None:
        """Restore VTE's original callback when the terminal body is unbound."""
        self.cancel_momentum()
        terminal = self._terminal_ref()
        if terminal is not None:
            for handler_id in self._terminal_handlers:
                terminal.disconnect(handler_id)
            self._terminal_handlers.clear()
            for controller in self._input_controllers:
                terminal.remove_controller(controller)
            self._input_controllers.clear()
            if getattr(terminal, "_ashy_application_scroll", None) is self:
                del terminal._ashy_application_scroll
        controller = self._controller_ref()
        if controller is None or not self._handler_id:
            return
        controller.disconnect(self._handler_id)
        controller.handler_unblock(self._native_handler_id)
        self._handler_id = 0

    def observe_output(self, chunk: bytes) -> None:
        """Cancel momentum before terminal mode changes reach VTE."""
        self._output_observed = True
        if self._mode_guard.feed(chunk):
            self.cancel_momentum()

    def cancel_momentum(self, *_args: Any) -> None:
        """Stop application inertia without changing native input handling."""
        if self._momentum is not None:
            self._momentum.cancel()

    def _bind_momentum(self, terminal: Vte.Terminal, scroll_engine: Any) -> None:
        self._momentum = ApplicationScrollMomentum(terminal, scroll_engine)
        terminal._ashy_application_scroll = self
        self._terminal_handlers = [
            terminal.connect("commit", self._on_commit),
            terminal.connect("unmap", self.cancel_momentum),
            terminal.connect("notify::has-focus", self._on_focus_changed),
        ]
        key_controller = Gtk.EventControllerKey.new()
        key_controller.connect("key-pressed", self._on_key_pressed)
        click_controller = Gtk.GestureClick.new()
        click_controller.set_button(0)
        click_controller.connect("pressed", self._on_pressed)
        self._input_controllers = [key_controller, click_controller]
        for controller in self._input_controllers:
            controller.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
            terminal.add_controller(controller)

    def _on_key_pressed(self, *_args: Any) -> bool:
        self.cancel_momentum()
        return Gdk.EVENT_PROPAGATE

    def _on_pressed(self, gesture: Gtk.GestureClick, *_args: Any) -> None:
        self.cancel_momentum()
        gesture.set_state(Gtk.EventSequenceState.DENIED)

    def _on_focus_changed(self, terminal: Vte.Terminal, _param: Any) -> None:
        if not terminal.has_focus():
            self.cancel_momentum()

    def _on_commit(self, _terminal: Vte.Terminal, text: str, _length: int) -> None:
        if self._reports is not None:
            self._reports.append(text)
        elif self._momentum is not None and not self._momentum.replaying:
            self.cancel_momentum()

    def _on_scroll(self, controller: Gtk.EventControllerScroll, dx: float, dy: float) -> bool:
        try:
            dy = self._scale_delta(controller, dy)
        except Exception as exc:
            _logger.warning(f"Application scroll scaling failed: {exc}")
        self._reports = []
        try:
            handled = self._dispatch_native(controller, dx, dy)
            self._track_momentum(controller, handled)
            return handled
        finally:
            self._reports = None

    def _track_momentum(self, controller: Gtk.EventControllerScroll, handled: bool) -> None:
        if self._momentum is None or not self._output_observed or not handled:
            return
        if normalize_scroll_mode(self._settings.get("terminal_scroll_mode")) == SCROLL_MODE_NATIVE:
            return
        event = controller.get_current_event()
        device = event.get_device() if event else None
        source = device.get_source() if device else Gdk.InputSource.MOUSE
        if controller.get_unit() == Gdk.ScrollUnit.SURFACE:
            source = Gdk.InputSource.TOUCHPAD
        if source == Gdk.InputSource.TOUCHPAD:
            self._momentum.track(self._reports, source)

    def _dispatch_native(
        self, controller: Gtk.EventControllerScroll, dx: float, dy: float
    ) -> bool:
        # Block this wrapper during re-emission, preserving the native return
        # value as well as the current GDK event used by VTE's callback.
        controller.handler_block(self._handler_id)
        controller.handler_unblock(self._native_handler_id)
        try:
            return controller.emit("scroll", dx, dy)
        finally:
            controller.handler_block(self._native_handler_id)
            controller.handler_unblock(self._handler_id)

    def _scale_delta(self, controller: Gtk.EventControllerScroll, dy: float) -> float:
        mode = normalize_scroll_mode(self._settings.get("terminal_scroll_mode"))
        terminal = self._terminal_ref()
        if mode == SCROLL_MODE_NATIVE or terminal is None:
            return dy
        adjustment = terminal.get_vadjustment()
        if adjustment.get_upper() - adjustment.get_lower() > adjustment.get_page_size():
            return dy
        unit = controller.get_unit()
        if unit not in (Gdk.ScrollUnit.SURFACE, Gdk.ScrollUnit.WHEEL):
            return dy
        multiplier = self._get_multiplier(controller, unit, terminal.get_char_height())
        scaled = dy * multiplier
        return scaled if math.isfinite(scaled) else dy

    def _get_multiplier(
        self, controller: Gtk.EventControllerScroll, unit: Gdk.ScrollUnit, row_height: int
    ) -> float:
        event = controller.get_current_event()
        device = event.get_device() if event else None
        touchpad = unit == Gdk.ScrollUnit.SURFACE or (
            device is not None and device.get_source() == Gdk.InputSource.TOUCHPAD
        )
        key = "touchpad_scroll_sensitivity" if touchpad else "mouse_scroll_sensitivity"
        sensitivity = float(self._settings.get(key, 50.0 if touchpad else 30.0))
        divisor = 25.0 if touchpad else 10.0
        if unit == Gdk.ScrollUnit.SURFACE:
            # GTK supplies logical pixels; VTE counts application wheel steps.
            divisor *= max(1, row_height)
        return sensitivity / divisor if sensitivity > 0 and math.isfinite(sensitivity) else 1.0
