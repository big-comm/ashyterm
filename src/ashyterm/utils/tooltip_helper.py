# ashyterm/utils/tooltip_helper.py
"""
Tooltip helper for showing helpful explanations on UI elements.
Provides a simple way to add custom tooltips with fade animation to any GTK widget.

STRATEGY: Root-Anchored Singleton Popover
To achieve instant speed (0ms), custom styling, AND stability:
1. We allow mouse tracking via EventControllerMotion.
2. We maintain a single Gtk.Popover attached to the top-level window (Root).
3. We do NOT reparent the popover. We simply recalculate the target widget's
   position relative to the Root and use `popover.set_pointing_to(rect)`.

This avoids segmentation faults caused by reparenting while bypassing native GTK delays.
"""

import logging
from typing import TYPE_CHECKING, Optional

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, GLib, Gtk

if TYPE_CHECKING:
    from ..settings.manager import SettingsManager

logger = logging.getLogger(__name__)


def _is_x11_backend() -> bool:
    """Check if we're running on X11 backend (not Wayland)."""
    try:
        display = Gdk.Display.get_default()
        if display is None:
            return False
        # Check display type name - X11 displays have "X11" in the type name
        display_type = type(display).__name__
        is_x11 = "X11" in display_type or "Gdk.X11Display" in str(type(display))
        return is_x11
    except Exception:
        return False


# Singleton instance
_tooltip_helper_instance: "TooltipHelper | None" = None
_app_instance = None


def get_tooltip_helper() -> "TooltipHelper":
    global _tooltip_helper_instance
    if _tooltip_helper_instance is None:
        _tooltip_helper_instance = TooltipHelper()
    return _tooltip_helper_instance


def init_tooltip_helper(
    settings_manager: Optional["SettingsManager"] = None, app=None
) -> "TooltipHelper":
    global _tooltip_helper_instance, _app_instance
    _app_instance = app
    _tooltip_helper_instance = TooltipHelper(settings_manager, app)
    return _tooltip_helper_instance


class TooltipHelper:
    """
    Manages custom tooltips using a Root-Anchored Gtk.Popover.
    """

    def __init__(self, settings_manager=None, app=None):
        self.settings_manager = settings_manager
        self.app = app

        self._use_native_tooltips = _is_x11_backend()

        # We keep one popover per Root Window.
        # Since widgets usually share the same root in this app, we can cache it.
        # However, to be robust, we'll store it on the Root object itself or look it up.
        # For simplicity in this app structure, we track the current active popover.
        self.active_popover: Optional[Gtk.Popover] = None
        self.active_label: Optional[Gtk.Label] = None

        self.active_widget = None
        self.show_timer_id = None
        self.hide_timer_id = None
        self._color_css_provider = None

    def update_colors(
        self,
        bg_color: Optional[str] = None,
        fg_color: Optional[str] = None,
        use_terminal_theme: bool = False,
    ):
        """Update tooltip colors to match the application theme."""
        if self._use_native_tooltips:
            return

        if use_terminal_theme and self.settings_manager:
            gtk_theme = self.settings_manager.get("gtk_theme", "")
            if gtk_theme == "terminal":
                scheme = self.settings_manager.get_color_scheme_data()
                bg_color = scheme.get(
                    "headerbar_background", scheme.get("background", "#1e1e1e")
                )
                fg_color = scheme.get("foreground", "#ffffff")
            else:
                try:
                    style_manager = Adw.StyleManager.get_default()
                    is_dark = style_manager.get_dark()
                    bg_color = "#1a1a1a" if is_dark else "#fafafa"
                    fg_color = "#ffffff" if is_dark else "#2e2e2e"
                except Exception:
                    bg_color = "#2a2a2a"
                    fg_color = "#ffffff"

        if not bg_color and not fg_color:
            return

        # Adjust background
        tooltip_bg = bg_color
        is_dark_theme = False
        if bg_color:
            tooltip_bg = self._adjust_tooltip_background(bg_color)
            # Detect theme
            try:
                hex_val = bg_color.lstrip("#")
                r = int(hex_val[0:2], 16)
                g = int(hex_val[2:4], 16)
                b = int(hex_val[4:6], 16)
                luminance = (0.299 * r + 0.587 * g + 0.114 * b) / 255
                is_dark_theme = luminance < 0.5
            except Exception:
                pass

        border_color = "#707070" if is_dark_theme else "#a0a0a0"

        # STYLE THE POPOVER DIRECTLY

        # 1. Main Popover Node (Surface) - Handles Opacity AND Spacing
        # We add padding here to create an invisible buffer zone around the content.
        # This guarantees spacing regardless of placement (Top/Bottom) or clipping.
        css_parts = [
            "popover.custom-tooltip-static {",
            "    background: transparent;",
            "    box-shadow: none;",
            "    padding: 9px;",  # <--- THE FIX: Transparent Spacing
            "    opacity: 0;",
            "    transition: opacity 200ms ease-in-out;",
            "}",
        ]

        # 2. Visible State (Applied to Popover)
        css_parts.append("popover.custom-tooltip-static.visible {")
        css_parts.append("    opacity: 1;")
        css_parts.append("}")

        # 3. Contents Node (Visual Bubble) - Handles Shape/Color
        css_parts.append("popover.custom-tooltip-static > contents {")
        if tooltip_bg:
            css_parts.append(f"    background-color: {tooltip_bg};")
        if fg_color:
            css_parts.append(f"    color: {fg_color};")

        css_parts.append("    padding: 6px 12px;")
        css_parts.append("    border-radius: 6px;")
        css_parts.append(f"    border: 1px solid {border_color};")
        css_parts.append("}")

        if fg_color:
            css_parts.append(
                f"popover.custom-tooltip-static label {{ color: {fg_color}; }}"
            )

        css = "\n".join(css_parts)

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
            self._color_css_provider = None

        provider = Gtk.CssProvider()
        provider.load_from_data(css.encode("utf-8"))
        try:
            Gtk.StyleContext.add_provider_for_display(
                display,
                provider,
                Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION + 100,
            )
            self._color_css_provider = provider
        except Exception:
            logger.exception("Failed to add CSS provider for tooltip colors")

    def _adjust_tooltip_background(self, bg_color: str) -> str:
        try:
            hex_val = bg_color.lstrip("#")
            r = int(hex_val[0:2], 16)
            g = int(hex_val[2:4], 16)
            b = int(hex_val[4:6], 16)
            luminance = (0.299 * r + 0.587 * g + 0.114 * b) / 255

            if luminance < 0.5:
                # Dark theme - lighten
                adjustment = 40
                r = min(255, r + adjustment)
                g = min(255, g + adjustment)
                b = min(255, b + adjustment)
            else:
                # Light theme - darken
                adjustment = 20
                r = max(0, r - adjustment)
                g = max(0, g - adjustment)
                b = max(0, b - adjustment)
            return f"#{r:02x}{g:02x}{b:02x}"
        except Exception:
            return bg_color

    def is_enabled(self) -> bool:
        """Check if tooltips are enabled in settings."""
        if self.settings_manager is None:
            return True
        return self.settings_manager.get("show_tooltips", True)

    def _get_shortcut_label(self, action_name: str) -> str | None:
        if not self.app:
            return None
        from ..helpers import accelerator_to_label

        for prefix in ("win", "app"):
            full_action = f"{prefix}.{action_name}"
            accels = self.app.get_accels_for_action(full_action)
            if accels:
                return accelerator_to_label(accels[0])
        return None

    def add_tooltip_with_shortcut(
        self,
        widget: Gtk.Widget,
        tooltip_text: str,
        action_name: str,
    ) -> None:
        if not tooltip_text:
            return

        shortcut = self._get_shortcut_label(action_name)
        full_text = f"{tooltip_text} ({shortcut})" if shortcut else tooltip_text

        if self._use_native_tooltips:
            widget.set_tooltip_text(full_text)
            return

        widget._custom_tooltip_base_text = tooltip_text
        widget._custom_tooltip_action = action_name
        widget._custom_tooltip_text = full_text
        widget.set_tooltip_text(None)

        self._add_controller(widget)

    def add_tooltip(self, widget: Gtk.Widget, tooltip_text: str) -> None:
        if not tooltip_text:
            return

        if self._use_native_tooltips:
            widget.set_tooltip_text(tooltip_text)
            return

        widget._custom_tooltip_text = tooltip_text
        widget.set_tooltip_text(None)
        self._add_controller(widget)

    def _add_controller(self, widget):
        # Prevent duplicated controllers
        if getattr(widget, "_has_custom_tooltip_controller", False):
            return

        controller = Gtk.EventControllerMotion.new()
        controller.connect("enter", self._on_enter, widget)
        controller.connect("leave", self._on_leave)
        widget.add_controller(controller)
        widget._has_custom_tooltip_controller = True

    def _clear_timer(self):
        if self.show_timer_id:
            GLib.source_remove(self.show_timer_id)
            self.show_timer_id = None
        if self.hide_timer_id:
            GLib.source_remove(self.hide_timer_id)
            self.hide_timer_id = None

    def _on_enter(self, controller, x, y, widget):
        if not self.is_enabled():
            return

        # Shortcut update logic
        base_text = getattr(widget, "_custom_tooltip_base_text", None)
        action_name = getattr(widget, "_custom_tooltip_action", None)
        if base_text and action_name:
            shortcut = self._get_shortcut_label(action_name)
            widget._custom_tooltip_text = (
                f"{base_text} ({shortcut})" if shortcut else base_text
            )

        self._clear_timer()
        self.active_widget = widget

        # 150ms delay for show effect
        self.show_timer_id = GLib.timeout_add(150, self._show_tooltip_impl)

    def _on_leave(self, controller):
        self._clear_timer()
        if self.active_widget:
            self.hide()
            self.active_widget = None

    def _get_root_popover(self, root: Gtk.Window) -> tuple[Gtk.Popover, Gtk.Label]:
        """
        Retrieves or creates the singleton popover attached to the given Root Window.
        """
        # We can store the popover on the root object to persist it
        if not hasattr(root, "_ashyterm_tooltip_popover"):
            popover = Gtk.Popover()
            # Disable arrow as requested
            popover.set_has_arrow(False)
            popover.set_position(Gtk.PositionType.TOP)

            # CRITICAL: Disable input
            popover.set_can_target(False)
            popover.set_focusable(False)
            popover.set_autohide(False)

            popover.add_css_class("custom-tooltip-static")

            label = Gtk.Label(wrap=True, max_width_chars=45)
            label.set_halign(Gtk.Align.CENTER)
            popover.set_child(label)

            # Attach to root
            popover.set_parent(root)

            root._ashyterm_tooltip_popover = (popover, label)

        return root._ashyterm_tooltip_popover

    def _show_tooltip_impl(self) -> bool:
        if not self.active_widget:
            return GLib.SOURCE_REMOVE

        try:
            root = self.active_widget.get_root()
            if not root or not isinstance(root, Gtk.Window):
                return GLib.SOURCE_REMOVE

            text = getattr(self.active_widget, "_custom_tooltip_text", None)
            if not text:
                return GLib.SOURCE_REMOVE

            popover, label = self._get_root_popover(root)

            # Update content
            label.set_text(text)

            # compute_bounds returns (bool, Graphene.Rect)
            success, graphene_rect = self.active_widget.compute_bounds(root)
            if success:
                # Convert Graphene.Rect (floats) to Gdk.Rectangle (ints)
                # Graphene.Rect has origin (Point) and size (Size)
                rect = Gdk.Rectangle()
                rect.x = int(graphene_rect.origin.x)
                rect.y = int(graphene_rect.origin.y)
                rect.width = int(graphene_rect.size.width)
                rect.height = int(graphene_rect.size.height)

                # NO MANUAL PADDING here. We rely on CSS padding.
                # This ensures the rect is valid (no negative coords) and prevents clamping.

                popover.set_pointing_to(rect)
                popover.popup()

                # Trigger fade-in
                popover.add_css_class("visible")

                self.active_popover = popover

        except Exception:
            logger.error("Error showing manual root-anchored tooltip", exc_info=True)
            pass

        self.show_timer_id = None
        return GLib.SOURCE_REMOVE

    def hide(self):
        """Hide the current tooltip with fade out."""
        # We find the existing popover on the active_widget's root if possible
        # Or just use the last known active_popover
        if self.active_popover:
            popover_to_hide = self.active_popover
            self.active_popover = None

            # Remove visible class to trigger fade-out
            try:
                popover_to_hide.remove_css_class("visible")
            except Exception:
                pass

            # Wait for animation then popdown
            # Using 300ms to allow 200ms transition to complete comfortably
            def do_popdown():
                try:
                    popover_to_hide.popdown()
                except Exception:
                    pass
                self.hide_timer_id = None
                return GLib.SOURCE_REMOVE

            # Clear any previous hide timer to be safe
            if self.hide_timer_id:
                GLib.source_remove(self.hide_timer_id)
            self.hide_timer_id = GLib.timeout_add(300, do_popdown)
