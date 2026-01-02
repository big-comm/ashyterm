# tooltip_helper.py
"""
Tooltip helper for showing helpful explanations on UI elements.
Provides a simple way to add custom tooltips with fade animation to any GTK widget.

PORTABLE VERSION:
- Self-contained (no external app dependencies).
- Widget-Anchored Popover for correct positioning (even inside other popovers).
- Custom CSS styling with Fade-Out.
- Auto-detects and updates colors from Adwaita StyleManager.
"""

import logging
from typing import Optional

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, GLib, Gtk

logger = logging.getLogger(__name__)

_tooltip_helper_instance: "TooltipHelper | None" = None


def get_tooltip_helper() -> "TooltipHelper":
    global _tooltip_helper_instance
    if _tooltip_helper_instance is None:
        _tooltip_helper_instance = TooltipHelper()
    return _tooltip_helper_instance


class TooltipHelper:
    """
    Manages custom tooltips using Widget-Anchored Gtk.Popover.
    Portable version: Depends only on GTK4/Adwaita.
    """

    def __init__(self):
        self.active_popover: Optional[Gtk.Popover] = None
        self.active_widget = None
        self.show_timer_id = None
        self.hide_timer_id = None
        self._color_css_provider = None
        self._colors_initialized = False
        self._tracked_windows: set = set()  # Windows we're monitoring for focus
        self._widgets_with_tooltips: set = set()  # All widgets with tooltips

        # Connect to Adwaita style manager for automatic theme updates
        try:
            style_manager = Adw.StyleManager.get_default()
            style_manager.connect("notify::dark", self._on_theme_changed)
            style_manager.connect("notify::color-scheme", self._on_theme_changed)
        except Exception:
            pass

    def _on_theme_changed(self, style_manager, pspec):
        """Auto-update colors when system theme changes."""
        GLib.idle_add(self._apply_default_colors)

    def _apply_default_colors(self):
        """Apply colors based on current Adwaita theme."""
        try:
            style_manager = Adw.StyleManager.get_default()
            is_dark = style_manager.get_dark()
            bg_color = "#1a1a1a" if is_dark else "#fafafa"
            fg_color = "#ffffff" if is_dark else "#2e2e2e"
        except Exception:
            bg_color = "#2a2a2a"
            fg_color = "#ffffff"

        self._apply_css(bg_color, fg_color)
        return GLib.SOURCE_REMOVE

    def _ensure_colors_initialized(self):
        """Ensure colors are set up before first tooltip display."""
        if not self._colors_initialized:
            self._apply_default_colors()
            self._colors_initialized = True

    def _apply_css(self, bg_color: str, fg_color: str):
        """Generate and apply CSS for tooltip styling."""
        tooltip_bg = self._adjust_tooltip_background(bg_color)
        is_dark_theme = self._is_dark_color(bg_color)
        border_color = "#707070" if is_dark_theme else "#a0a0a0"

        css = f"""
popover.custom-tooltip-static {{
    background: transparent;
    box-shadow: none;
    padding: 12px;
    opacity: 0;
    transition: opacity 200ms ease-in-out;
}}
popover.custom-tooltip-static.visible {{
    opacity: 1;
}}
popover.custom-tooltip-static > contents {{
    background-color: {tooltip_bg};
    color: {fg_color};
    padding: 6px 12px;
    border-radius: 6px;
    border: 1px solid {border_color};
}}
popover.custom-tooltip-static label {{
    color: {fg_color};
}}
"""
        display = Gdk.Display.get_default()
        if not display:
            return

        if self._color_css_provider:
            try:
                Gtk.StyleContext.remove_provider_for_display(
                    display, self._color_css_provider
                )
            except Exception:
                pass

        provider = Gtk.CssProvider()
        provider.load_from_data(css.encode("utf-8"))
        try:
            Gtk.StyleContext.add_provider_for_display(
                display, provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION + 100
            )
            self._color_css_provider = provider
        except Exception:
            logger.exception("Failed to add CSS provider for tooltip colors")

    def _adjust_tooltip_background(self, bg_color: str) -> str:
        try:
            hex_val = bg_color.lstrip("#")
            r, g, b = (
                int(hex_val[0:2], 16),
                int(hex_val[2:4], 16),
                int(hex_val[4:6], 16),
            )
            luminance = (0.299 * r + 0.587 * g + 0.114 * b) / 255
            adj = 40 if luminance < 0.5 else -20
            r, g, b = (
                max(0, min(255, r + adj)),
                max(0, min(255, g + adj)),
                max(0, min(255, b + adj)),
            )
            return f"#{r:02x}{g:02x}{b:02x}"
        except Exception:
            return bg_color

    def _is_dark_color(self, color: str) -> bool:
        try:
            hex_val = color.lstrip("#")
            r, g, b = (
                int(hex_val[0:2], 16),
                int(hex_val[2:4], 16),
                int(hex_val[4:6], 16),
            )
            return (0.299 * r + 0.587 * g + 0.114 * b) / 255 < 0.5
        except Exception:
            return True

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

        if widget.get_realized():
            on_realize(widget)
        else:
            widget.connect("realize", on_realize)

    def _on_window_active_changed(self, window, pspec):
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
                except Exception:
                    pass

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

    def _on_enter(self, controller, x, y, widget):
        self._clear_timer()
        self.active_widget = widget
        self.show_timer_id = GLib.timeout_add(150, self._show_tooltip_impl)

    def _on_leave(self, controller):
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
            if not self.active_widget.get_mapped():
                self.show_timer_id = None
                return GLib.SOURCE_REMOVE

            # Check if the widget's window is the active/focused window
            # Don't show tooltip if another window (like a dialog) is on top
            root = self.active_widget.get_root()
            if root and isinstance(root, Gtk.Window):
                if not root.is_active():
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

            try:
                popover_to_hide.remove_css_class("visible")
            except Exception:
                pass

            if immediate:
                # Hide immediately (no animation wait)
                try:
                    popover_to_hide.popdown()
                except Exception:
                    pass
            else:
                # Wait for animation then popdown
                def do_popdown():
                    try:
                        popover_to_hide.popdown()
                    except Exception:
                        pass
                    self.hide_timer_id = None
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
            except Exception:
                pass
