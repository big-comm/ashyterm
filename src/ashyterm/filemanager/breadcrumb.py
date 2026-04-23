# ashyterm/filemanager/breadcrumb.py
"""Breadcrumb bar construction + path math for the file manager.

Two concerns:

* :func:`rebuild_breadcrumb` — wipe a :class:`Gtk.Box` and repopulate
  it with one clickable button per path component, separated by ``›``.
* :func:`compute_navigation_path` — given the current directory and
  the clicked :class:`FileItem`, compute the new absolute path the
  file manager should navigate to. ``..`` is the one case that
  needs care (``parent('/')`` stays at root).

Keeping both here lets us unit-test path-math edge cases (deep roots,
``..`` at ``/``) without mounting the file manager.
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk

from ..helpers import clear_children
from ..utils.accessibility import set_label as a11y_label
from ..utils.translation_utils import _
from .models import FileItem


def compute_navigation_path(current_path: str, item: FileItem) -> str:
    """Compute the new absolute path when ``item`` is activated.

    * ``..`` at root returns ``""`` (caller treats empty as "no-op").
    * ``..`` elsewhere returns ``Path(current_path).parent``.
    * Any other name is joined onto ``current_path``, normalizing
      trailing slashes so we never double them up.
    """
    if item.name == "..":
        if current_path == "/":
            return ""
        return str(Path(current_path).parent)
    base_path = current_path.rstrip("/")
    return f"{base_path}/{item.name}"


def rebuild_breadcrumb(
    box: Gtk.Box,
    current_path: str,
    *,
    on_clicked: Callable[[Gtk.Button, str], None],
) -> None:
    """Rebuild the breadcrumb bar inside ``box`` for ``current_path``.

    Root (``/`` or empty) renders a single ``/`` button. Every other
    path becomes ``/ › a › b › c`` where each segment is a button whose
    click handler receives the cumulative path it represents.
    """
    clear_children(box)

    path = Path(current_path)
    # ``Path('')`` has empty ``parts`` too; treat it as root.
    if not path.parts or path.parts == ("/",):
        btn = Gtk.Button(label="/")
        btn.add_css_class("flat")
        btn.connect("clicked", on_clicked, "/")
        a11y_label(btn, _("Navigate to root"))
        box.append(btn)
        return

    accumulated = Path()
    for i, part in enumerate(path.parts):
        display_name = part if i > 0 else "/"
        if i == 0 and part == "/":
            accumulated = Path(part)
        else:
            accumulated = accumulated / part
            separator = Gtk.Label(label="›")
            separator.add_css_class("dim-label")
            box.append(separator)

        btn = Gtk.Button(label=display_name)
        btn.add_css_class("flat")
        btn.connect("clicked", on_clicked, str(accumulated))
        a11y_label(btn, _("Navigate to {}").format(display_name))
        box.append(btn)
