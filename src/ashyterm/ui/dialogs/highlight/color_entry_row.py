"""ColorEntryRow widget for editing individual color entries in highlight rules."""

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GObject, Gtk

from ....settings.highlights import get_highlight_manager
from ....utils.accessibility import set_label as a11y_label
from ....utils.tooltip_helper import get_tooltip_helper
from ....utils.translation_utils import _
from ..base_dialog import BaseDialog, create_icon_button
from ._constants import (
    BACKGROUND_COLOR_OPTIONS,
    LOGICAL_COLOR_OPTIONS,
    TEXT_EFFECT_OPTIONS,
)


class ColorEntryRow(Adw.ActionRow):
    """
    A row for editing a single color in the colors list.

    Provides:
    - Dropdown to select base foreground color
    - Toggle buttons for text effects (bold, italic, underline, etc.)
    - Dropdown to select background color (optional)
    - Delete button to remove the row

    The color string is composed from base color + active effects + background.
    Example: "bold italic red on_blue"
    """

    __gsignals__ = {
        "color-changed": (GObject.SignalFlags.RUN_FIRST, None, ()),
        "remove-requested": (GObject.SignalFlags.RUN_FIRST, None, ()),
    }

    def __init__(self, group_index: int, color_name: str = "white"):
        """
        Initialize the color entry row.

        Args:
            group_index: The capture group index (1-based for display)
            color_name: Initial logical color name (may include modifiers and bg color)
        """
        super().__init__()
        self._group_index = group_index
        self._css_provider = None

        # Parse initial color string into components
        self._fg_color, self._bg_color, self._effects = self._parse_color_string(
            color_name or "white"
        )

        self.set_title(_("Group {}").format(group_index))

        self._effect_toggles: dict[str, Gtk.ToggleButton] = {}
        self._setup_ui()
        self._load_color()

    def _parse_color_string(self, color_string: str) -> tuple:
        """
        Parse a color string into foreground color, background color, and effects.

        Args:
            color_string: e.g., "bold italic red on_blue", "green", "underline white"

        Returns:
            Tuple of (foreground_color, background_color, set of effects)
        """
        parts = color_string.lower().split()
        fg_color = "white"
        bg_color = ""
        effects = set()

        known_effects = {opt[0] for opt in TEXT_EFFECT_OPTIONS}

        for part in parts:
            if part.startswith("on_"):
                bg_color = part
            elif part in known_effects:
                effects.add(part)
            else:
                fg_color = part

        return fg_color, bg_color, effects

    def _setup_ui(self) -> None:
        """Setup the row UI components with horizontal toolbar layout."""
        main_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        main_box.set_valign(Gtk.Align.CENTER)

        # Color Preview Box (prefix)
        self._color_box = Gtk.Box()
        self._color_box.set_size_request(28, 28)
        self._color_box.set_valign(Gtk.Align.CENTER)
        self._color_box.add_css_class("circular")
        self.add_prefix(self._color_box)

        # Foreground Color Dropdown
        fg_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        fg_label = Gtk.Label(label=_("Color:"))
        fg_label.add_css_class("dim-label")
        fg_box.append(fg_label)

        self._fg_dropdown = Gtk.DropDown()
        self._fg_model = Gtk.StringList()
        for color_id, color_label in LOGICAL_COLOR_OPTIONS:
            self._fg_model.append(color_label)
        self._fg_dropdown.set_model(self._fg_model)
        self._fg_dropdown.connect(
            BaseDialog.SIGNAL_NOTIFY_SELECTED, self._on_fg_color_selected
        )
        fg_box.append(self._fg_dropdown)
        main_box.append(fg_box)

        sep1 = Gtk.Separator(orientation=Gtk.Orientation.VERTICAL)
        sep1.set_margin_start(4)
        sep1.set_margin_end(4)
        main_box.append(sep1)

        # Effect Toggle Buttons (linked box)
        effects_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        effects_box.add_css_class("linked")

        for effect_id, effect_label, icon_name in TEXT_EFFECT_OPTIONS:
            toggle = Gtk.ToggleButton()
            toggle.set_icon_name(icon_name)
            a11y_label(toggle, effect_label)
            get_tooltip_helper().add_tooltip(toggle, effect_label)
            toggle.set_valign(Gtk.Align.CENTER)
            toggle.connect("toggled", self._on_effect_toggled, effect_id)
            effects_box.append(toggle)
            self._effect_toggles[effect_id] = toggle

        main_box.append(effects_box)

        sep2 = Gtk.Separator(orientation=Gtk.Orientation.VERTICAL)
        sep2.set_margin_start(4)
        sep2.set_margin_end(4)
        main_box.append(sep2)

        # Background Color Dropdown
        bg_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        bg_label = Gtk.Label(label=_("Bg:"))
        bg_label.add_css_class("dim-label")
        bg_box.append(bg_label)

        self._bg_dropdown = Gtk.DropDown()
        self._bg_model = Gtk.StringList()
        for color_id, color_label in BACKGROUND_COLOR_OPTIONS:
            self._bg_model.append(color_label)
        self._bg_dropdown.set_model(self._bg_model)
        self._bg_dropdown.connect(
            BaseDialog.SIGNAL_NOTIFY_SELECTED, self._on_bg_color_selected
        )
        bg_box.append(self._bg_dropdown)
        main_box.append(bg_box)

        self.add_suffix(main_box)

        # Remove Button
        remove_btn = create_icon_button(
            "user-trash-symbolic",
            tooltip=_("Remove"),
            on_clicked=lambda b: self.emit("remove-requested"),
            flat=True,
            valign=Gtk.Align.CENTER,
        )
        self.add_suffix(remove_btn)

    def _load_color(self) -> None:
        """Load the initial colors and effects into the UI controls."""
        fg_lower = self._fg_color.lower()
        for idx, (color_id, color_label) in enumerate(LOGICAL_COLOR_OPTIONS):
            if color_id == fg_lower:
                self._fg_dropdown.set_selected(idx)
                break
        else:
            for idx, (color_id, _label) in enumerate(LOGICAL_COLOR_OPTIONS):
                if color_id == "white":
                    self._fg_dropdown.set_selected(idx)
                    break

        bg_lower = self._bg_color.lower()
        for idx, (color_id, color_label) in enumerate(BACKGROUND_COLOR_OPTIONS):
            if color_id == bg_lower:
                self._bg_dropdown.set_selected(idx)
                break

        for effect_id, toggle in self._effect_toggles.items():
            toggle.set_active(effect_id in self._effects)

        self._update_color_preview()

    def _on_fg_color_selected(self, dropdown: Gtk.DropDown, _pspec) -> None:
        """Handle foreground color selection change."""
        idx = dropdown.get_selected()
        if idx != Gtk.INVALID_LIST_POSITION and idx < len(LOGICAL_COLOR_OPTIONS):
            self._fg_color = LOGICAL_COLOR_OPTIONS[idx][0]
            self._update_color_preview()
            self.emit("color-changed")

    def _on_bg_color_selected(self, dropdown: Gtk.DropDown, _pspec) -> None:
        """Handle background color selection change."""
        idx = dropdown.get_selected()
        if idx != Gtk.INVALID_LIST_POSITION and idx < len(BACKGROUND_COLOR_OPTIONS):
            self._bg_color = BACKGROUND_COLOR_OPTIONS[idx][0]
            self._update_color_preview()
            self.emit("color-changed")

    def _on_effect_toggled(self, toggle: Gtk.ToggleButton, effect_id: str) -> None:
        """Handle text effect toggle."""
        if toggle.get_active():
            self._effects.add(effect_id)
        else:
            self._effects.discard(effect_id)
        self._update_color_preview()
        self.emit("color-changed")

    def _update_color_preview(self) -> None:
        """Update the color preview box showing foreground, background, and effects."""
        manager = get_highlight_manager()

        fg_hex = manager.resolve_color(self._fg_color)

        bg_hex = None
        if self._bg_color:
            bg_color_name = (
                self._bg_color[3:]
                if self._bg_color.startswith("on_")
                else self._bg_color
            )
            bg_hex = manager.resolve_color(bg_color_name)

        fill_style = f"background-color: {fg_hex};"

        if bg_hex:
            base_width = 4
            border_args = f"{bg_hex}"
        else:
            base_width = 2
            border_args = "alpha(currentColor, 0.3)"

        border_width = 5 if "bold" in self._effects else base_width
        line_style = "dashed" if "italic" in self._effects else "solid"

        border_style_value = f"{border_width}px {line_style} {border_args}"

        css_provider = Gtk.CssProvider()
        css = f"""
        .color-preview {{
            {fill_style}
            border-radius: 50%;
            border: {border_style_value};
        }}
        """
        css_provider.load_from_data(css.encode("utf-8"))

        context = self._color_box.get_style_context()
        if self._css_provider:
            context.remove_provider(self._css_provider)

        context.add_provider(css_provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        self._color_box.add_css_class("color-preview")
        self._css_provider = css_provider

    @property
    def color_name(self) -> str:
        """
        Get the combined color name (effects + foreground + optional background).

        Returns a string like "bold italic red on_blue" that can be passed
        to resolve_color_to_ansi() for rendering.
        """
        parts = []

        for effect_id, _label, _icon in TEXT_EFFECT_OPTIONS:
            if effect_id in self._effects:
                parts.append(effect_id)

        parts.append(self._fg_color)

        if self._bg_color:
            parts.append(self._bg_color)

        return " ".join(parts)

    @property
    def group_index(self) -> int:
        """Get the group index."""
        return self._group_index
