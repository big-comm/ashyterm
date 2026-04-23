# tooltip_helper.py
"""Custom tooltip widget (Popover + fade). Adwaita-themed, no extra deps."""

import logging
from typing import Optional

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, GLib, Gtk
from .logger import log_swallowed_exception

logger = logging.getLogger(__name__)

_tooltip_helper_instance: "TooltipHelper | None" = None


def get_tooltip_helper() -> "TooltipHelper":
    global _tooltip_helper_instance
    if _tooltip_helper_instance is None:
        _tooltip_helper_instance = TooltipHelper()
    return _tooltip_helper_instance


class TooltipHelper:
    """Custom tooltips via widget-anchored Gtk.Popover with fade animations."""

    def __init__(self):
        self.active_popover: Optional[Gtk.Popover] = None
        self.active_widget = None
        self.show_timer_id = None
        self.hide_timer_id = None
        self.closing_popover = None  # popover mid-fade; kept for cleanup
        self._color_css_provider = None
        self._colors_initialized = False
        self._tracked_windows: set = set()
        self._widgets_with_tooltips: set = set()

        # Connect to Adwaita style manager for automatic theme updates
        try:
            style_manager = Adw.StyleManager.get_default()
            style_manager.connect("notify::dark", self._on_theme_changed)
            style_manager.connect("notify::color-scheme", self._on_theme_changed)
        except Exception as exc:
            log_swallowed_exception(exc)

    def _on_theme_changed(self, style_manager, _pspec):
        """Auto-update colors when system theme changes."""
        GLib.idle_add(self._apply_default_colors)

    def _apply_default_colors(self):
        """Apply CSS using GTK named colors for theme-aware tooltips."""
        self._apply_css()
        return GLib.SOURCE_REMOVE

    def _ensure_colors_initialized(self):
        """Ensure colors are set up before first tooltip display."""
        if not self._colors_initialized:
            self._apply_css()
            self._colors_initialized = True

    def _apply_css(self):
        """Generate and apply CSS for tooltip styling using GTK named colors.

        Uses GTK4 named colors (@popover_bg_color, @window_fg_color, etc.)
        so colors adapt automatically to system theme — no hardcoded hex.
        """
        css = """
popover.custom-tooltip-static {
    background: transparent;
    box-shadow: none;
    padding: 12px;
    opacity: 0;
    transition: opacity 200ms ease-in-out;
}
popover.custom-tooltip-static.visible {
    opacity: 1;
}
popover.custom-tooltip-static > contents {
    background-color: @popover_bg_color;
    color: @window_fg_color;
    padding: 6px 12px;
    border-radius: 6px;
    border: 1px solid @borders;
}
popover.custom-tooltip-static label {
    color: @window_fg_color;
}
"""
        display = Gdk.Display.get_default()
        if not display:
            return

        if self._color_css_provider:
            try:
                Gtk.StyleContext.remove_provider_for_display(
                    display, self._color_css_provider
                )
            except Exception as exc:
                log_swallowed_exception(exc)

        from ..utils.css_helpers import apply_inline_css

        provider = apply_inline_css(
            css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION + 100, display,
        )
        try:
            self._color_css_provider = provider
        except Exception:
            logger.exception("Failed to add CSS provider for tooltip colors")

    def add_tooltip(self, widget: Gtk.Widget, tooltip_text: str) -> None:
        if not tooltip_text:
            return
        widget._custom_tooltip_text = tooltip_text
        widget.set_tooltip_text(None)
        self._add_controller(widget)

        # Track the widget for cleanup
        self._widgets_with_tooltips.add(widget)

        # Monitor the widget's window for focus changes
        self._setup_window_focus_tracking(widget)

    def _setup_window_focus_tracking(self, widget: Gtk.Widget) -> None:
        """Setup focus tracking on the widget's root window."""

        def on_realize(w):
            root = w.get_root()
            if root and isinstance(root, Gtk.Window):
                if root not in self._tracked_windows:
                    self._tracked_windows.add(root)
                    root.connect("notify::is-active", self._on_window_active_changed)
                    # Also track window state changes (maximize/fullscreen)
                    # to hide tooltips immediately when window state changes
                    root.connect("notify::maximized", self._on_window_state_changed)
                    root.connect("notify::fullscreened", self._on_window_state_changed)

        if widget.get_realized():
            on_realize(widget)
        else:
            widget.connect("realize", on_realize)

    def _on_window_state_changed(self, window, _pspec):
        """Hide all tooltips immediately when window state changes (maximize/fullscreen).

        This prevents the tooltip popover from interfering with input events
        during window state transitions.
        """
        self._clear_timer()
        self.hide(immediate=True)
        self.active_widget = None

        # Force popdown all popovers to ensure they don't capture events
        for widget in list(self._widgets_with_tooltips):
            try:
                if hasattr(widget, "_custom_tooltip_popover"):
                    popover, _ = widget._custom_tooltip_popover
                    popover.popdown()
            except Exception as exc:
                log_swallowed_exception(exc)

    def _on_window_active_changed(self, window, _pspec):
        """Hide all tooltips when any tracked window loses focus."""
        if not window.get_property("is-active"):
            # Window lost focus - hide all tooltips immediately
            self._clear_timer()
            self.hide(immediate=True)
            self.active_widget = None

            # Also popdown any lingering popovers on widgets in this window
            for widget in list(self._widgets_with_tooltips):
                try:
                    if hasattr(widget, "_custom_tooltip_popover"):
                        popover, _ = widget._custom_tooltip_popover
                        popover.popdown()
                except Exception as exc:
                    log_swallowed_exception(exc)

    def _add_controller(self, widget):
        if getattr(widget, "_has_custom_tooltip_controller", False):
            return

        # Motion controller for enter/leave
        controller = Gtk.EventControllerMotion.new()
        controller.connect("enter", self._on_enter, widget)
        controller.connect("leave", self._on_leave)
        widget.add_controller(controller)

        # Click controller - hide tooltip when widget is clicked
        click_controller = Gtk.GestureClick.new()
        click_controller.connect("pressed", self._on_click)
        widget.add_controller(click_controller)

        widget._has_custom_tooltip_controller = True

    def _on_click(self, gesture, n_press, x, y):
        """Hide tooltip immediately when widget is clicked."""
        self._clear_timer()
        self.hide(immediate=True)
        self.active_widget = None

    def _clear_timer(self):
        if self.show_timer_id:
            GLib.source_remove(self.show_timer_id)
            self.show_timer_id = None
        if self.hide_timer_id:
            GLib.source_remove(self.hide_timer_id)
            self.hide_timer_id = None

        # If we had a closing popover pending from a hide timer we just cancelled,
        # we must force it to close immediately to prevent it from getting stuck
        if self.closing_popover:
            try:
                self.closing_popover.popdown()
                self.closing_popover.remove_css_class("visible")
            except Exception as exc:
                log_swallowed_exception(exc)
            self.closing_popover = None

    def _on_enter(self, controller, x, y, widget):
        # If we are entering a new widget while another is still active (even if fading),
        # force close the previous one immediately
        if self.active_widget and self.active_widget != widget:
            self.hide(immediate=True)

        self._clear_timer()
        self.active_widget = widget
        self.show_timer_id = GLib.timeout_add(150, self._show_tooltip_impl)

    def _on_leave(self, controller):
        # Only hide if we are leaving the currently active widget
        # This prevents stale leave events from hiding a NEW tooltip we just entered
        widget = controller.get_widget()
        if self.active_widget == widget:
            self._clear_timer()
            if self.active_widget:
                self.hide()
                self.active_widget = None

    def _get_widget_popover(self, widget: Gtk.Widget) -> tuple[Gtk.Popover, Gtk.Label]:
        """Get or create a tooltip popover attached directly to the widget."""
        if not hasattr(widget, "_custom_tooltip_popover"):
            popover = Gtk.Popover()
            popover.set_has_arrow(False)
            popover.set_position(Gtk.PositionType.TOP)
            popover.set_can_target(False)
            popover.set_focusable(False)
            popover.set_autohide(False)
            popover.add_css_class("custom-tooltip-static")

            label = Gtk.Label(wrap=True, max_width_chars=45)
            label.set_halign(Gtk.Align.CENTER)
            popover.set_child(label)

            popover.set_parent(widget)

            widget._custom_tooltip_popover = (popover, label)
        return widget._custom_tooltip_popover

    def _show_tooltip_impl(self) -> bool:
        if not self.active_widget:
            return GLib.SOURCE_REMOVE

        # Ensure CSS is applied before showing
        self._ensure_colors_initialized()

        try:
            text = getattr(self.active_widget, "_custom_tooltip_text", None)
            if not text:
                return GLib.SOURCE_REMOVE

            # Check if the widget is actually visible and mapped
            mapped = self.active_widget.get_mapped()
            if not mapped:
                self.show_timer_id = None
                return GLib.SOURCE_REMOVE

            # Check if the widget's window is the active/focused window
            # Don't show tooltip if another window (like a dialog) is on top
            root = self.active_widget.get_root()
            if root and isinstance(root, Gtk.Window):
                active = root.is_active()
                if not active:
                    # Window is not active, don't show tooltip
                    self.show_timer_id = None
                    return GLib.SOURCE_REMOVE

            popover, label = self._get_widget_popover(self.active_widget)
            label.set_text(text)

            # Point to the entire widget (0,0 to width,height)
            alloc = self.active_widget.get_allocation()
            rect = Gdk.Rectangle()
            rect.x = 0
            rect.y = 0
            rect.width = alloc.width
            rect.height = alloc.height

            popover.set_pointing_to(rect)
            popover.popup()
            popover.set_visible(True)
            popover.add_css_class("visible")

            self.active_popover = popover

        except Exception:
            logger.error("Error showing tooltip", exc_info=True)

        self.show_timer_id = None
        return GLib.SOURCE_REMOVE

    def hide(self, immediate: bool = False):
        """Hide the current tooltip. If immediate=True, skip animation."""
        if self.active_popover:
            popover_to_hide = self.active_popover
            self.active_popover = None

            # Track this popover as closing
            self.closing_popover = popover_to_hide

            try:
                popover_to_hide.remove_css_class("visible")
            except Exception as exc:
                log_swallowed_exception(exc)

            if immediate:
                # Hide immediately (no animation wait)
                try:
                    popover_to_hide.popdown()
                except Exception as exc:
                    log_swallowed_exception(exc)
            else:
                # Wait for animation then popdown
                def do_popdown():
                    try:
                        popover_to_hide.popdown()
                    except Exception as exc:
                        log_swallowed_exception(exc)
                    self.hide_timer_id = None
                    self.closing_popover = None
                    return GLib.SOURCE_REMOVE

                if self.hide_timer_id:
                    GLib.source_remove(self.hide_timer_id)
                self.hide_timer_id = GLib.timeout_add(300, do_popdown)

    def hide_all(self):
        """Hide all tooltips from all tracked widgets immediately.

        Useful to call when opening dialogs or switching focus.
        """
        self._clear_timer()
        self.hide(immediate=True)
        self.active_widget = None

        # Popdown all tooltip popovers
        for widget in list(self._widgets_with_tooltips):
            try:
                if hasattr(widget, "_custom_tooltip_popover"):
                    popover, _ = widget._custom_tooltip_popover
                    popover.popdown()
            except Exception as exc:
                log_swallowed_exception(exc)
