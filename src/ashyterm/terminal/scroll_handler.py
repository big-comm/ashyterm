# ashyterm/terminal/scroll_handler.py
"""Scroll handling delegate for TabManager.

Handles custom scroll sensitivity, touchpad kinetic scrolling,
Ctrl+scroll font zoom, and smart scroll-on-output.
"""

import math
import time
import weakref
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Optional

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Vte", "3.91")
from gi.repository import Gdk, GLib, Gtk, Vte

from ..settings.scrolling import (
    SCROLL_MODE_AUTOMATIC,
    SCROLL_MODE_CUSTOM,
    SCROLL_MODE_NATIVE,
    normalize_scroll_mode,
)
from ..utils.logger import get_logger

if TYPE_CHECKING:
    from .tabs import TabManager

_LEGACY_SCROLL_UNIT = object()


@dataclass
class _ScrollBinding:
    """Custom controller attached above one native scrolled window."""

    host_ref: weakref.ReferenceType[Gtk.Widget]
    controller: Gtk.EventControllerScroll
    unmap_handler_id: int


class ScrollHandler:
    """Manages terminal scroll behavior including kinetic scrolling."""

    def __init__(self, tab_manager: "TabManager") -> None:
        self.tm = tab_manager
        self.logger = get_logger("ashyterm.tabs.scroll")
        self._bindings: weakref.WeakKeyDictionary[
            Gtk.ScrolledWindow, _ScrollBinding
        ] = weakref.WeakKeyDictionary()
        settings = self.tm.terminal_manager.settings_manager
        if hasattr(settings, "add_change_listener"):
            settings.add_change_listener(self._on_setting_changed)

    # -- public helpers -------------------------------------------------------

    def scroll_to_widget(self, widget: Gtk.Widget) -> None:
        """Scrolls the tab bar to make the given widget visible."""
        hadjustment = self.tm.scrolled_tab_bar.get_hadjustment()
        if not hadjustment:
            return

        coords = widget.translate_coordinates(self.tm.scrolled_tab_bar, 0, 0)
        if coords is None:
            return

        widget_x, _ = coords
        widget_width = widget.get_width()
        viewport_width = self.tm.scrolled_tab_bar.get_width()

        current_scroll_value = hadjustment.get_value()

        if widget_x < 0:
            hadjustment.set_value(current_scroll_value + widget_x)
        elif widget_x + widget_width > viewport_width:
            hadjustment.set_value(
                current_scroll_value + (widget_x + widget_width - viewport_width)
            )

    def bind_scroll_controller(
        self, host: Gtk.Widget, sw: Gtk.ScrolledWindow
    ) -> None:
        """Bind custom-first scrolling above ``sw`` without altering GTK's controller."""
        existing = self._bindings.get(sw)
        if existing and existing.host_ref() is host:
            return
        if existing:
            self._detach_binding(sw, existing)

        controller = Gtk.EventControllerScroll.new(
            Gtk.EventControllerScrollFlags.VERTICAL
        )
        controller.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        sw_ref = weakref.ref(sw)
        controller.connect("scroll-begin", self._on_scroll_begin, sw_ref)
        controller.connect("scroll", self._on_terminal_scroll, sw_ref)
        controller.connect("scroll-end", self._on_scroll_end, sw_ref)
        host.add_controller(controller)
        unmap_handler_id = host.connect("unmap", self._on_scroll_host_unmap, sw_ref)
        self._bindings[sw] = _ScrollBinding(
            host_ref=weakref.ref(host),
            controller=controller,
            unmap_handler_id=unmap_handler_id,
        )

    def replace_sw_scroll_controller(self, sw: Gtk.ScrolledWindow) -> None:
        """Compatibility shim that now preserves the native GTK controller."""
        host = getattr(sw, "_ashy_scroll_host", None)
        if not isinstance(host, Gtk.Widget):
            self.logger.warning("Cannot bind terminal scroll controller without a host")
            return
        self.bind_scroll_controller(host, sw)

    def unbind_scrolled_window(self, sw: Gtk.ScrolledWindow) -> None:
        """Remove AshyTerm's controller and cancel pending kinetic work."""
        binding = self._bindings.pop(sw, None)
        if binding:
            self._detach_binding(sw, binding)

    # -- signal callbacks -----------------------------------------------------

    def on_terminal_contents_changed(self, terminal: Vte.Terminal) -> None:
        """Handles smart scrolling on new terminal output."""
        if not self.tm.terminal_manager.settings_manager.get("scroll_on_output", True):
            return

        scrolled_window = terminal.get_parent()
        if not isinstance(scrolled_window, Gtk.ScrolledWindow):
            return

        adjustment = scrolled_window.get_vadjustment()
        if not adjustment:
            return

        is_at_bottom = (
            adjustment.get_value() + adjustment.get_page_size()
            >= adjustment.get_upper() - 1.0
        )

        if is_at_bottom:
            if getattr(terminal, "_scroll_pending", False):
                return

            terminal._scroll_pending = True

            def scroll_to_end():
                terminal._scroll_pending = False
                adjustment.set_value(
                    adjustment.get_upper() - adjustment.get_page_size()
                )
                return GLib.SOURCE_REMOVE

            GLib.idle_add(scroll_to_end)

    # -- internal methods -----------------------------------------------------

    def _on_scroll_begin(self, controller, sw_ref) -> None:
        """Reset gesture routing and stop momentum from the previous gesture."""
        controller._ashy_scroll_route = None
        if sw := sw_ref():
            self._cancel_kinetic_scroll(sw)

    def _on_scroll_end(self, controller, _sw_ref) -> None:
        """Release per-gesture routing state."""
        controller._ashy_scroll_route = None

    def _on_terminal_scroll(self, controller, _dx, dy, sw_ref):
        """Run custom scrolling first and propagate to GTK when needed."""
        mode = self._get_scroll_mode()
        sw = sw_ref()
        if not sw:
            return Gdk.EVENT_PROPAGATE

        try:
            state = controller.get_current_event_state()
            if state & Gdk.ModifierType.CONTROL_MASK:
                self._handle_scroll_zoom(dy)
                return Gdk.EVENT_STOP

            if mode == SCROLL_MODE_NATIVE:
                self._cancel_kinetic_scroll(sw)
                return Gdk.EVENT_PROPAGATE

            route = getattr(controller, "_ashy_scroll_route", None)
            if mode == SCROLL_MODE_AUTOMATIC and route == SCROLL_MODE_NATIVE:
                return Gdk.EVENT_PROPAGATE

            unit = self._get_scroll_unit(controller)
            handled = self._apply_custom_scroll(sw, controller, dy, unit)
            if handled:
                if mode == SCROLL_MODE_AUTOMATIC and unit == Gdk.ScrollUnit.SURFACE:
                    controller._ashy_scroll_route = SCROLL_MODE_CUSTOM
                return Gdk.EVENT_STOP

            if mode == SCROLL_MODE_CUSTOM or route == SCROLL_MODE_CUSTOM:
                return Gdk.EVENT_STOP

            if unit == Gdk.ScrollUnit.SURFACE:
                controller._ashy_scroll_route = SCROLL_MODE_NATIVE
            self._cancel_kinetic_scroll(sw)
        except Exception as e:
            self.logger.warning(f"Error handling custom scroll: {e}")
            self._cancel_kinetic_scroll(sw)
            if mode == SCROLL_MODE_CUSTOM:
                return Gdk.EVENT_STOP
        return Gdk.EVENT_PROPAGATE

    def _handle_scroll_zoom(self, dy: float) -> None:
        """Handle Ctrl+Scroll font size change."""
        delta = 1 if dy < 0 else -1 if dy > 0 else 0
        tm = self.tm.terminal_manager
        if delta and hasattr(tm, "settings_manager"):
            font_string = tm.settings_manager.get("font", "Monospace 12")
            parts = font_string.rsplit(" ", 1)
            try:
                family, size = parts[0], int(parts[1])
            except (IndexError, ValueError):
                family, size = "Monospace", 12
            new_size = max(6, min(72, size + delta))
            if new_size != size:
                GLib.idle_add(tm._apply_font_size_change, family, new_size)

    def _get_scroll_mode(self) -> str:
        settings = self.tm.terminal_manager.settings_manager
        return normalize_scroll_mode(
            settings.get("terminal_scroll_mode", SCROLL_MODE_AUTOMATIC)
        )

    def _get_scroll_unit(self, controller) -> Any:
        get_unit = getattr(controller, "get_unit", None)
        if not callable(get_unit):
            return _LEGACY_SCROLL_UNIT
        try:
            unit = get_unit()
        except AttributeError:
            return _LEGACY_SCROLL_UNIT
        except Exception:
            return None
        if unit in (Gdk.ScrollUnit.WHEEL, Gdk.ScrollUnit.SURFACE):
            return unit
        return None

    def _get_scroll_input_source(self, controller):
        """Determine the input device source for a scroll event."""
        event = controller.get_current_event()
        device = event.get_device() if event else None
        return device.get_source() if device else Gdk.InputSource.MOUSE

    def _calculate_scroll_amount(
        self, dy, vadjustment, source, unit
    ) -> Optional[float]:
        """Calculate a finite delta using the input unit reported by GTK."""
        delta = self._finite_float(dy)
        if delta is None or delta == 0:
            return None

        sm = self.tm.terminal_manager.settings_manager
        if source == Gdk.InputSource.TOUCHPAD:
            sensitivity = sm.get("touchpad_scroll_sensitivity", 30.0)
            divisor = 50.0
        else:
            sensitivity = sm.get("mouse_scroll_sensitivity", 30.0)
            divisor = 10.0
        sensitivity_value = self._finite_float(sensitivity)
        if sensitivity_value is None or sensitivity_value <= 0:
            return None

        if unit in (Gdk.ScrollUnit.WHEEL, _LEGACY_SCROLL_UNIT):
            step = self._finite_float(vadjustment.get_step_increment())
            if step is None or step <= 0:
                return None
            delta *= step
        elif unit != Gdk.ScrollUnit.SURFACE:
            return None

        amount = delta * sensitivity_value / divisor
        return amount if math.isfinite(amount) and amount != 0 else None

    def _apply_custom_scroll(self, sw, controller, dy, unit) -> bool:
        """Apply one custom scroll event and verify the adjustment response."""
        adjustment = sw.get_vadjustment()
        if not adjustment or unit is None:
            return False

        source = self._get_scroll_input_source(controller)
        amount = self._calculate_scroll_amount(dy, adjustment, source, unit)
        if amount is None:
            return False

        values = [
            self._finite_float(adjustment.get_value()),
            self._finite_float(adjustment.get_lower()),
            self._finite_float(adjustment.get_upper()),
            self._finite_float(adjustment.get_page_size()),
        ]
        if any(value is None for value in values):
            return False
        before, lower, upper, page_size = values
        maximum = max(lower, upper - page_size)
        target = min(max(before + amount, lower), maximum)
        if math.isclose(target, before, abs_tol=1e-9):
            return self._is_expected_boundary(before, lower, maximum, amount)

        adjustment.set_value(target)
        after = self._finite_float(adjustment.get_value())
        if after is None or math.isclose(after, before, abs_tol=1e-9):
            return False
        if (amount > 0 and after < before) or (amount < 0 and after > before):
            return False

        self._track_kinetic_scroll(sw, source, after - before)
        return True

    @staticmethod
    def _finite_float(value: Any) -> Optional[float]:
        if isinstance(value, bool):
            return None
        try:
            number = float(value)
        except (TypeError, ValueError, OverflowError):
            return None
        return number if math.isfinite(number) else None

    @staticmethod
    def _is_expected_boundary(
        value: float, lower: float, maximum: float, amount: float
    ) -> bool:
        return (amount < 0 and value <= lower) or (amount > 0 and value >= maximum)

    def _track_kinetic_scroll(self, sw, source, scroll_amount):
        """Track scroll velocity for kinetic deceleration (touchpad only)."""
        kinetic_intensity = self._get_kinetic_intensity()
        if not kinetic_intensity or source != Gdk.InputSource.TOUCHPAD:
            return

        now = time.monotonic()
        history = getattr(sw, "_k_history", None)
        if history is None:
            sw._k_history = []
            history = sw._k_history

        history.append((now, scroll_amount))
        cutoff = now - 0.15
        sw._k_history = [(t, d) for t, d in history if t > cutoff]

        anim = getattr(sw, "_k_anim", None)
        if anim:
            GLib.source_remove(anim)
            sw._k_anim = None

        idle = getattr(sw, "_k_idle", None)
        if idle:
            GLib.source_remove(idle)
        sw._k_idle = GLib.timeout_add(60, self._start_kinetic_deceleration, sw)

    def _start_kinetic_deceleration(self, sw):
        """Called ~60 ms after the last touchpad scroll event (finger lift)."""
        sw._k_idle = None
        history = getattr(sw, "_k_history", [])
        if len(history) < 2:
            sw._k_history = []
            return GLib.SOURCE_REMOVE

        total = sum(d for _, d in history)
        duration = max(history[-1][0] - history[0][0], 0.016)
        velocity = total / duration
        sw._k_history = []

        if abs(velocity) < 20:
            return GLib.SOURCE_REMOVE

        sw._k_vel = velocity
        sw._k_time = time.monotonic()
        intensity = self._get_kinetic_intensity()
        if not intensity:
            return GLib.SOURCE_REMOVE
        sw._k_friction = 0.80 + max(1, min(100, intensity)) * 0.0018
        sw._k_anim = GLib.timeout_add(16, self._kinetic_tick, sw)
        return GLib.SOURCE_REMOVE

    def _kinetic_tick(self, sw):
        """Applies one frame of kinetic deceleration (~60 fps)."""
        friction = getattr(sw, "_k_friction", 0.92)
        MIN_VEL = 15.0

        now = time.monotonic()
        dt = now - sw._k_time
        sw._k_time = now

        vadjustment = sw.get_vadjustment()
        if not vadjustment or abs(sw._k_vel) < MIN_VEL:
            sw._k_anim = None
            return GLib.SOURCE_REMOVE

        vadjustment.set_value(vadjustment.get_value() + sw._k_vel * dt)
        sw._k_vel *= friction ** (dt * 62.5)

        return GLib.SOURCE_CONTINUE

    def _get_kinetic_intensity(self) -> int:
        raw = self.tm.terminal_manager.settings_manager.get("kinetic_scrolling", 50)
        if isinstance(raw, bool):
            return 50 if raw else 0
        try:
            return max(0, min(100, int(raw)))
        except (TypeError, ValueError, OverflowError):
            return 0

    def _cancel_kinetic_scroll(self, sw: Gtk.ScrolledWindow) -> None:
        for attribute in ("_k_idle", "_k_anim"):
            source_id = getattr(sw, attribute, None)
            if source_id:
                try:
                    GLib.source_remove(source_id)
                except Exception:
                    pass
            setattr(sw, attribute, None)
        sw._k_history = []
        sw._k_vel = 0.0

    def _detach_binding(
        self, sw: Gtk.ScrolledWindow, binding: _ScrollBinding
    ) -> None:
        self._cancel_kinetic_scroll(sw)
        host = binding.host_ref()
        if host is None:
            return
        try:
            host.disconnect(binding.unmap_handler_id)
        except Exception:
            pass
        try:
            host.remove_controller(binding.controller)
        except Exception as e:
            self.logger.debug(f"Failed to detach custom scroll controller: {e}")

    def _on_scroll_host_unmap(self, _host, sw_ref) -> None:
        if sw := sw_ref():
            self._cancel_kinetic_scroll(sw)

    def _on_setting_changed(self, key: str, _old_value, _new_value) -> None:
        if key not in {"terminal_scroll_mode", "kinetic_scrolling"}:
            return
        for sw, binding in list(self._bindings.items()):
            binding.controller._ashy_scroll_route = None
            self._cancel_kinetic_scroll(sw)
