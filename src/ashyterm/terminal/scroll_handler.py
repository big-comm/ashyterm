# ashyterm/terminal/scroll_handler.py
"""Scroll handling delegate for TabManager.

Handles custom scroll sensitivity, touchpad kinetic scrolling,
Ctrl+scroll font zoom, and smart scroll-on-output.
"""

import time
import weakref
from typing import TYPE_CHECKING

from gi.repository import Gdk, GLib, Gtk, Vte

from ..utils.logger import get_logger

if TYPE_CHECKING:
    from .tabs import TabManager


class ScrollHandler:
    """Manages terminal scroll behavior including kinetic scrolling."""

    def __init__(self, tab_manager: "TabManager") -> None:
        self.tm = tab_manager
        self.logger = get_logger("ashyterm.tabs.scroll")

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

    def replace_sw_scroll_controller(self, sw: Gtk.ScrolledWindow) -> None:
        """Replace ScrolledWindow's built-in scroll controller with ours.

        The SW's default EventControllerScroll (CAPTURE phase) would
        consume all scroll events before child controllers can see them.
        Replacing it gives us full control over sensitivity and kinetic.
        """
        model = sw.observe_controllers()
        to_remove = []
        for i in range(model.get_n_items()):
            ctrl = model.get_item(i)
            if isinstance(ctrl, Gtk.EventControllerScroll):
                to_remove.append(ctrl)
        for ctrl in to_remove:
            sw.remove_controller(ctrl)

        our_ctrl = Gtk.EventControllerScroll.new(
            Gtk.EventControllerScrollFlags.VERTICAL
        )
        our_ctrl.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        our_ctrl.connect("scroll", self._on_terminal_scroll)
        sw.add_controller(our_ctrl)

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

    def _on_terminal_scroll(self, controller, dx, dy):
        """Handles all terminal scroll events with custom sensitivity."""
        try:
            sw = controller.get_widget()
            if not isinstance(sw, Gtk.ScrolledWindow):
                return Gdk.EVENT_PROPAGATE

            state = controller.get_current_event_state()
            if state & Gdk.ModifierType.CONTROL_MASK:
                self._handle_scroll_zoom(dy)
                return Gdk.EVENT_STOP

            vadjustment = sw.get_vadjustment()
            if not vadjustment:
                return Gdk.EVENT_PROPAGATE

            source = self._get_scroll_input_source(controller)
            scroll_amount = self._calculate_scroll_amount(dy, vadjustment, source)
            vadjustment.set_value(vadjustment.get_value() + scroll_amount)

            self._track_kinetic_scroll(sw, source, scroll_amount)

            return Gdk.EVENT_STOP
        except Exception as e:
            self.logger.warning(f"Error handling custom scroll: {e}")
        return Gdk.EVENT_PROPAGATE

    def _handle_scroll_zoom(self, dy):
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

    def _get_scroll_input_source(self, controller):
        """Determine the input device source for a scroll event."""
        event = controller.get_current_event()
        device = event.get_device() if event else None
        return device.get_source() if device else Gdk.InputSource.MOUSE

    def _calculate_scroll_amount(self, dy, vadjustment, source):
        """Calculate scroll delta based on device type and sensitivity settings."""
        sm = self.tm.terminal_manager.settings_manager
        if source == Gdk.InputSource.TOUCHPAD:
            sensitivity_factor = sm.get("touchpad_scroll_sensitivity", 30.0) / 50.0
        else:
            sensitivity_factor = sm.get("mouse_scroll_sensitivity", 30.0) / 10.0
        step = vadjustment.get_step_increment()
        return dy * step * sensitivity_factor

    def _track_kinetic_scroll(self, sw, source, scroll_amount):
        """Track scroll velocity for kinetic deceleration (touchpad only)."""
        sm = self.tm.terminal_manager.settings_manager
        kinetic_raw = sm.get("kinetic_scrolling", 50)
        if isinstance(kinetic_raw, bool):
            kinetic_raw = 50 if kinetic_raw else 0
        kinetic_intensity = int(kinetic_raw)
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
        raw = self.tm.terminal_manager.settings_manager.get("kinetic_scrolling", 50)
        intensity = 50 if isinstance(raw, bool) and raw else int(raw) if raw else 50
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
