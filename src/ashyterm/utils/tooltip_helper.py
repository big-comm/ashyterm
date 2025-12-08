# ashyterm/utils/tooltip_helper.py
"""
Tooltip helper for showing helpful explanations on UI elements.
Provides a simple way to add custom tooltips with fade animation to any GTK widget.
Replaces the default GTK tooltip system with a more visually appealing popover-based approach.
"""

from typing import TYPE_CHECKING

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
from gi.repository import Gdk, GLib, Gtk

if TYPE_CHECKING:
    from ..settings.manager import SettingsManager

# Singleton instance
_tooltip_helper_instance: "TooltipHelper | None" = None
_app_instance = None


def get_tooltip_helper() -> "TooltipHelper":
    """
    Get the global TooltipHelper instance.

    Returns:
        The singleton TooltipHelper instance.
    """
    global _tooltip_helper_instance
    if _tooltip_helper_instance is None:
        _tooltip_helper_instance = TooltipHelper()
    return _tooltip_helper_instance


def init_tooltip_helper(
    settings_manager: "SettingsManager" = None, app=None
) -> "TooltipHelper":
    """
    Initialize the global TooltipHelper with a settings manager.

    Should be called once during application startup with the settings manager.

    Args:
        settings_manager: The settings manager for checking tooltip preferences.
        app: The Gtk.Application instance for looking up keyboard shortcuts.

    Returns:
        The initialized TooltipHelper instance.
    """
    global _tooltip_helper_instance, _app_instance
    _app_instance = app
    _tooltip_helper_instance = TooltipHelper(settings_manager, app)
    return _tooltip_helper_instance


class TooltipHelper:
    """
    Manages a single, reusable Gtk.Popover to display custom tooltips.

    Uses a singleton popover to prevent state conflicts. The animation is handled
    by CSS classes, and the fade-in is reliably triggered by hooking into the
    popover's "map" signal. This avoids race conditions with the GTK renderer.

    Usage:
        tooltip_helper = TooltipHelper(settings_manager)
        tooltip_helper.add_tooltip(widget, "My tooltip text")
    """

    def __init__(self, settings_manager=None, app=None):
        """
        Initialize the tooltip helper.

        Args:
            settings_manager: Optional settings manager to check if tooltips are enabled.
            app: Optional Gtk.Application for looking up keyboard shortcuts.
        """
        self.settings_manager = settings_manager
        self.app = app

        # State machine variables
        self.active_widget = None
        self.show_timer_id = None
        self._is_cleaning_up = False
        self._suppressed = False  # When True, tooltips are temporarily suppressed

        # The single, reusable popover
        self.popover = Gtk.Popover()
        self.popover.set_autohide(False)
        self.popover.set_has_arrow(True)
        self.popover.set_position(Gtk.PositionType.TOP)

        self.label = Gtk.Label(
            wrap=True,
            max_width_chars=50,
            margin_start=12,
            margin_end=12,
            margin_top=8,
            margin_bottom=8,
            halign=Gtk.Align.START,
        )
        self.popover.set_child(self.label)

        # CSS for tooltip animation is now loaded globally from components.css
        # Classes: .tooltip-popover, .tooltip-popover.visible
        self.popover.add_css_class("tooltip-popover")

        # Separate provider for color styling (can be updated dynamically)
        self._color_css_provider = None

        # Connect to the "map" signal to trigger the fade-in animation
        self.popover.connect("map", self._on_popover_map)

    def update_colors(
        self,
        bg_color: str = None,
        fg_color: str = None,
        use_terminal_theme: bool = False,
    ):
        """
        Update tooltip colors to match the application theme.

        Uses the same luminance check as manager.py: the main terminal background
        color is used for the luminance check, but headerbar_background is used
        for the actual tooltip background color (if luminance check passes).

        Args:
            bg_color: Background color hex (e.g., "#1e1e1e")
            fg_color: Foreground/text color hex (e.g., "#ffffff")
            use_terminal_theme: If True and settings_manager is available,
                               use terminal color scheme colors.
        """
        # Color used for luminance check (same as manager.py)
        luminance_check_color = None

        if use_terminal_theme and self.settings_manager:
            gtk_theme = self.settings_manager.get("gtk_theme", "")
            if gtk_theme == "terminal":
                scheme = self.settings_manager.get_color_scheme_data()
                # Use main background for luminance check (same as manager.py)
                luminance_check_color = scheme.get("background", "#000000")
                # Use headerbar_background for the actual tooltip color
                bg_color = scheme.get(
                    "headerbar_background", scheme.get("background", "#1e1e1e")
                )
                fg_color = scheme.get("foreground", "#ffffff")

        # Skip if no colors specified
        if not bg_color and not fg_color:
            return

        # Check luminance using the same color as manager.py (main background)
        # Skip custom styling if too dark (< 5%) to avoid readability issues
        check_color = luminance_check_color or bg_color
        if check_color:
            try:
                hex_val = check_color.lstrip("#")
                r, g, b = (
                    int(hex_val[0:2], 16) / 255,
                    int(hex_val[2:4], 16) / 255,
                    int(hex_val[4:6], 16) / 255,
                )
                luminance = 0.299 * r + 0.587 * g + 0.114 * b
                if luminance < 0.05:
                    # Too dark - remove any existing custom styling and use Adwaita defaults
                    if self._color_css_provider:
                        try:
                            Gtk.StyleContext.remove_provider_for_display(
                                Gdk.Display.get_default(), self._color_css_provider
                            )
                            self._color_css_provider = None
                        except Exception:
                            pass
                    return
            except (ValueError, IndexError):
                pass

        # Build CSS with higher specificity selectors
        css_parts = ["popover.tooltip-popover > contents {"]
        if bg_color:
            css_parts.append(f"    background-color: {bg_color};")
        if fg_color:
            css_parts.append(f"    color: {fg_color};")
        css_parts.append("}")

        # Also style the label with high specificity
        if fg_color:
            css_parts.append(f"popover.tooltip-popover label {{ color: {fg_color}; }}")

        css = "\n".join(css_parts)

        # Remove existing color provider if any
        if self._color_css_provider:
            try:
                Gtk.StyleContext.remove_provider_for_display(
                    Gdk.Display.get_default(), self._color_css_provider
                )
            except Exception:
                pass

        # Add new color provider
        provider = Gtk.CssProvider()
        provider.load_from_data(css.encode("utf-8"))
        Gtk.StyleContext.add_provider_for_display(
            Gdk.Display.get_default(),
            provider,
            Gtk.STYLE_PROVIDER_PRIORITY_USER,
        )
        self._color_css_provider = provider

    def _on_popover_map(self, popover):
        """
        Triggered when the popover is mapped (shown).
        Adds the 'visible' CSS class to initiate the fade-in animation.
        """
        # A small delay ensures the CSS transition triggers correctly
        GLib.timeout_add(10, lambda: popover.add_css_class("visible") or False)

    def is_enabled(self) -> bool:
        """Check if tooltips are enabled in settings."""
        if self.settings_manager is None:
            return True
        return self.settings_manager.get("show_tooltips", True)

    def _get_shortcut_label(self, action_name: str) -> str | None:
        """
        Get the human-readable label for a keyboard shortcut.

        Args:
            action_name: The action name (e.g., "toggle-search", "new-local-tab")

        Returns:
            The shortcut label (e.g., "Ctrl+Shift+F") or None if no shortcut.
        """
        if not self.app:
            return None

        # Import here to avoid circular dependency
        from ..helpers import accelerator_to_label

        # Try both win. and app. prefixes
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
        """
        Add a tooltip that includes the keyboard shortcut for an action.

        The shortcut is dynamically looked up, so if the user changes it,
        the tooltip will automatically reflect the new shortcut.

        Args:
            widget: The GTK widget to add the tooltip to.
            tooltip_text: The base tooltip text.
            action_name: The action name to look up shortcut for (e.g., "toggle-search").
        """
        if not tooltip_text:
            return

        # Store base text and action name for dynamic lookup
        widget._custom_tooltip_base_text = tooltip_text
        widget._custom_tooltip_action = action_name

        # Build initial tooltip text with shortcut
        shortcut = self._get_shortcut_label(action_name)
        if shortcut:
            widget._custom_tooltip_text = f"{tooltip_text} ({shortcut})"
        else:
            widget._custom_tooltip_text = tooltip_text

        # Clear any existing default tooltip
        widget.set_tooltip_text(None)

        # Add motion controller for enter/leave events
        motion_controller = Gtk.EventControllerMotion.new()
        motion_controller.connect("enter", self._on_enter_with_shortcut, widget)
        motion_controller.connect("leave", self._on_leave)
        widget.add_controller(motion_controller)

    def add_tooltip(self, widget: Gtk.Widget, tooltip_text: str) -> None:
        """
        Connects a widget to the tooltip management system with custom text.

        This replaces the widget's default tooltip with a custom animated popover.
        The widget's existing tooltip_text property will be cleared.

        Args:
            widget: The GTK widget to add the tooltip to.
            tooltip_text: The text to display in the tooltip.
        """
        if not tooltip_text:
            return

        # Store tooltip text on the widget
        widget._custom_tooltip_text = tooltip_text

        # Clear any existing default tooltip
        widget.set_tooltip_text(None)

        # Add motion controller for enter/leave events
        motion_controller = Gtk.EventControllerMotion.new()
        motion_controller.connect("enter", self._on_enter, widget)
        motion_controller.connect("leave", self._on_leave)
        widget.add_controller(motion_controller)

    def _clear_timer(self):
        """Clear any pending show timer."""
        if self.show_timer_id:
            GLib.source_remove(self.show_timer_id)
            self.show_timer_id = None

    def _on_enter(self, controller, x, y, widget):
        """Handle mouse entering a widget with a tooltip."""
        if self._is_cleaning_up:
            return

        if not self.is_enabled():
            return

        # If suppressed, ignore this enter event
        if self._suppressed:
            return

        if self.active_widget == widget:
            return

        self._clear_timer()
        self._hide_tooltip()

        self.active_widget = widget
        # Show tooltip after 350ms delay
        self.show_timer_id = GLib.timeout_add(350, self._show_tooltip)

    def _on_enter_with_shortcut(self, controller, x, y, widget):
        """Handle mouse entering a widget with a dynamic shortcut tooltip."""
        if self._is_cleaning_up:
            return

        if not self.is_enabled():
            return

        # If suppressed, ignore this enter event
        if self._suppressed:
            return

        if self.active_widget == widget:
            return

        # Update tooltip text with current shortcut before showing
        base_text = getattr(widget, "_custom_tooltip_base_text", "")
        action_name = getattr(widget, "_custom_tooltip_action", None)

        if base_text and action_name:
            shortcut = self._get_shortcut_label(action_name)
            if shortcut:
                widget._custom_tooltip_text = f"{base_text} ({shortcut})"
            else:
                widget._custom_tooltip_text = base_text

        self._clear_timer()
        self._hide_tooltip()

        self.active_widget = widget
        # Show tooltip after 350ms delay
        self.show_timer_id = GLib.timeout_add(350, self._show_tooltip)

    def _on_leave(self, controller):
        """Handle mouse leaving a widget with a tooltip."""
        if self._is_cleaning_up:
            return

        self._clear_timer()

        # Clear suppression when mouse leaves
        self._suppressed = False

        if self.active_widget:
            self._hide_tooltip(animate=True)
            self.active_widget = None

    def _show_tooltip(self) -> bool:
        """Show the tooltip popover for the active widget."""
        if self._is_cleaning_up:
            return GLib.SOURCE_REMOVE

        # Don't show if suppressed
        if self._suppressed:
            self.show_timer_id = None
            return GLib.SOURCE_REMOVE

        if not self.active_widget:
            return GLib.SOURCE_REMOVE

        tooltip_text = getattr(self.active_widget, "_custom_tooltip_text", None)
        if not tooltip_text:
            return GLib.SOURCE_REMOVE

        # Configure and show the popover
        self.label.set_text(tooltip_text)

        # Ensure popover is unparented before setting new parent
        if self.popover.get_parent():
            self.popover.unparent()

        self.popover.set_parent(self.active_widget)
        self.popover.popup()

        self.show_timer_id = None
        return GLib.SOURCE_REMOVE

    def _hide_tooltip(self, animate: bool = False):
        """
        Hide the tooltip popover.

        Args:
            animate: If True, wait for fade-out animation before cleanup.
        """
        if self._is_cleaning_up:
            return

        if not self.popover.is_visible():
            # Still ensure unparenting even if not visible
            if self.popover.get_parent():
                try:
                    self.popover.unparent()
                except Exception:
                    pass
            return

        def do_cleanup():
            if self._is_cleaning_up:
                return GLib.SOURCE_REMOVE
            try:
                self.popover.popdown()
            except Exception:
                pass
            if self.popover.get_parent():
                try:
                    self.popover.unparent()
                except Exception:
                    pass
            return GLib.SOURCE_REMOVE

        # Trigger fade-out animation by removing .visible class
        self.popover.remove_css_class("visible")

        if animate:
            # Wait for animation to finish before cleaning up
            GLib.timeout_add(200, do_cleanup)
        else:
            do_cleanup()

    def hide(self):
        """
        Force hide any visible tooltip immediately.
        Also suppresses the tooltip from reappearing until mouse leaves.
        """
        self._clear_timer()
        self._suppressed = True
        self._hide_tooltip(animate=False)
        self.active_widget = None
