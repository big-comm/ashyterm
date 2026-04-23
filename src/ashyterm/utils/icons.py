# ashyterm/utils/icons.py
"""Icon loader: bundled SVGs (default) with system-theme fallback.

Public API: :func:`icon_image`, :func:`icon_button`, :func:`set_button_icon`.
"""

import os
from pathlib import Path
from typing import Optional

import gi

from .tooltip_helper import get_tooltip_helper

gi.require_version("Gtk", "4.0")
from gi.repository import Gio, Gtk

# Icon directory paths (in order of priority)
_ICON_PATHS = [
    "/usr/share/ashyterm/icons",
    str(Path(__file__).parent.parent / "icons"),  # src/ashyterm/icons
]

# Cached icon directory (resolved once)
_icon_dir: Optional[str] = None

# Flag set by app.py during startup based on icon_theme_strategy setting
# True = use bundled SVG icons, False = use system icons only
_use_bundled_icons: bool = True  # Default to bundled for performance


def _get_icon_dir() -> Optional[str]:
    """Get the bundled icons directory (cached)."""
    global _icon_dir
    if _icon_dir is None:
        for path in _ICON_PATHS:
            if os.path.isdir(path):
                _icon_dir = path
                break
    return _icon_dir


def get_icon_path(icon_name: str) -> Optional[str]:
    """Path to the bundled SVG, or ``None`` if missing."""
    icon_dir = _get_icon_dir()
    if not icon_dir:
        return None

    # Normalize icon name - add .svg if not present
    if not icon_name.endswith(".svg"):
        icon_name = f"{icon_name}.svg"

    icon_path = os.path.join(icon_dir, icon_name)
    if os.path.isfile(icon_path):
        return icon_path
    return None


def has_bundled_icon(icon_name: str) -> bool:
    return get_icon_path(icon_name) is not None


def _create_image_from_file(icon_path: str, size: int) -> Gtk.Image:
    """File → Gio.FileIcon → Gtk.Image (so symbolic recoloring works)."""
    gfile = Gio.File.new_for_path(icon_path)
    file_icon = Gio.FileIcon.new(gfile)
    image = Gtk.Image.new_from_gicon(file_icon)
    image.set_pixel_size(size)
    return image


def create_icon_image(
    icon_name: str,
    size: int = 16,
    use_bundled: Optional[bool] = None,
    fallback_to_system: bool = True,
) -> Gtk.Image:
    """Gtk.Image from ``icon_name``. Bundled SVG first, then system theme."""
    if use_bundled is None:
        use_bundled = _use_bundled_icons

    if use_bundled:
        icon_path = get_icon_path(icon_name)
        if icon_path:
            image = _create_image_from_file(icon_path, size)
            if icon_name.endswith("-symbolic"):
                image.add_css_class("icon-symbolic")
            return image

    if fallback_to_system:
        image = Gtk.Image.new_from_icon_name(icon_name)
        image.set_pixel_size(size)
        return image

    return Gtk.Image()


def _create_button_from_bundled_icon(icon_name: str, size: int) -> Optional[Gtk.Button]:
    icon_path = get_icon_path(icon_name)
    if not icon_path:
        return None
    image = _create_image_from_file(icon_path, size)
    if icon_name.endswith("-symbolic"):
        image.add_css_class("icon-symbolic")
    button = Gtk.Button()
    button.set_child(image)
    return button


def _create_button_from_system_icon(icon_name: str, size: int) -> Gtk.Button:
    button = Gtk.Button.new_from_icon_name(icon_name)
    child = button.get_child()
    if isinstance(child, Gtk.Image):
        child.set_pixel_size(size)
    return button


def _apply_button_tooltip(button: Gtk.Button, tooltip: Optional[str]) -> None:
    """Tooltip + a11y label so screen readers announce the button."""
    if not tooltip:
        return
    helper = get_tooltip_helper()
    if helper:
        helper.add_tooltip(button, tooltip)
    else:
        button.set_tooltip_text(tooltip)
    button.update_property(
        [Gtk.AccessibleProperty.LABEL],
        [tooltip],
    )


def _apply_button_styling(
    button: Gtk.Button,
    css_classes: Optional[list],
    flat: bool,
    valign: Optional[Gtk.Align],
) -> None:
    if flat:
        button.add_css_class("flat")
    if css_classes:
        for css_class in css_classes:
            button.add_css_class(css_class)
    if valign is not None:
        button.set_valign(valign)


def create_icon_button(
    icon_name: str,
    size: int = 16,
    use_bundled: Optional[bool] = None,
    tooltip: Optional[str] = None,
    css_classes: Optional[list] = None,
    flat: bool = False,
    on_clicked=None,
    callback_args: tuple = (),
    valign: Optional[Gtk.Align] = None,
) -> Gtk.Button:
    """Gtk.Button with icon, tooltip, optional CSS/flat/valign, and handler."""
    if use_bundled is None:
        use_bundled = _use_bundled_icons

    button = None
    if use_bundled:
        button = _create_button_from_bundled_icon(icon_name, size)

    if button is None:
        button = _create_button_from_system_icon(icon_name, size)

    _apply_button_tooltip(button, tooltip)
    _apply_button_styling(button, css_classes, flat, valign)

    if on_clicked:
        button.connect("clicked", on_clicked, *callback_args)

    return button


def set_image_from_icon(
    image: Gtk.Image,
    icon_name: str,
    size: int = 16,
    use_bundled: Optional[bool] = None,
) -> None:
    """Swap an existing Gtk.Image's content to ``icon_name``."""
    if use_bundled is None:
        use_bundled = _use_bundled_icons

    if use_bundled:
        icon_path = get_icon_path(icon_name)
        if icon_path:
            gfile = Gio.File.new_for_path(icon_path)
            file_icon = Gio.FileIcon.new(gfile)
            image.set_from_gicon(file_icon)
            image.set_pixel_size(size)
            if icon_name.endswith("-symbolic"):
                image.add_css_class("icon-symbolic")
            return

    image.set_from_icon_name(icon_name)
    image.set_pixel_size(size)


def set_button_icon(
    button: Gtk.Button,
    icon_name: str,
    size: int = 16,
    use_bundled: Optional[bool] = None,
) -> None:
    """Swap a button's icon. ``set_icon_name()`` only works for system icons."""
    child = button.get_child()
    if isinstance(child, Gtk.Image):
        set_image_from_icon(child, icon_name, size, use_bundled)
    else:
        image = create_icon_image(icon_name, size, use_bundled)
        button.set_child(image)


icon_image = create_icon_image
icon_button = create_icon_button
