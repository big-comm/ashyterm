# ashyterm/ui/sidebar_builder.py
"""Sidebar construction for the main window.

The sidebar shows a session tree (the main content) with a toolbar of
action buttons on top and a search entry at the bottom. A
``Gtk.Stack`` wraps everything so the tree can be swapped with an
inline context menu when the user right-clicks.

Like :mod:`header_bar_builder`, this module takes the owning
``WindowUIBuilder`` and stores widgets back onto it so the rest of
the window can keep talking to ``builder.add_session_button``,
``builder.sidebar_search_entry``, etc.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk

from ..utils.accessibility import set_label as a11y_label
from ..utils.icons import icon_button
from ..utils.translation_utils import _

if TYPE_CHECKING:
    from .window_ui import WindowUIBuilder


def _build_action_toolbar(builder: "WindowUIBuilder") -> Gtk.Box:
    """Build the top toolbar with add/edit/save/remove icon buttons.

    Buttons are stored on ``builder`` so click handlers wired in
    ``window.py`` can reach them.
    """
    toolbar = Gtk.Box(
        orientation=Gtk.Orientation.HORIZONTAL,
        spacing=4,
        halign=Gtk.Align.CENTER,
        margin_top=6,
        margin_bottom=6,
        css_classes=["sidebar-toolbar"],
    )

    # (attr_name, icon, accessibility+tooltip label, extra css class or None)
    specs: list[tuple[str, str, str, str | None]] = [
        ("add_session_button", "list-add-symbolic", _("Add Session"), None),
        ("add_folder_button", "folder-new-symbolic", _("Add Folder"), None),
        ("edit_button", "document-edit-symbolic", _("Edit Selected"), None),
        (
            "save_layout_button",
            "document-save-symbolic",
            _("Save Current Layout"),
            None,
        ),
        (
            "remove_button",
            "user-trash-symbolic",
            _("Remove Selected"),
            "destructive",
        ),
    ]
    for attr, icon, label, extra_class in specs:
        button = icon_button(icon)
        a11y_label(button, label)
        builder.tooltip_helper.add_tooltip(button, label)
        if extra_class:
            button.add_css_class(extra_class)
        setattr(builder, attr, button)
        toolbar.append(button)

    return toolbar


def _build_normal_view(builder: "WindowUIBuilder") -> Gtk.Box:
    """Build the default sidebar view (toolbar + session tree + search)."""
    normal_view = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
    normal_view.append(_build_action_toolbar(builder))

    # Session tree fills the remaining vertical space.
    scrolled = Gtk.ScrolledWindow(vexpand=True)
    scrolled.set_child(builder.session_tree.get_widget())
    scrolled.add_css_class("sidebar-session-tree")
    normal_view.append(scrolled)

    # Search entry docked at the bottom.
    search_container = Gtk.Box(
        orientation=Gtk.Orientation.VERTICAL, css_classes=["sidebar-search"]
    )
    builder.sidebar_search_entry = Gtk.SearchEntry(
        placeholder_text=_("Search sessions...")
    )
    a11y_label(builder.sidebar_search_entry, _("Search sessions"))
    builder.sidebar_search_entry.set_margin_start(6)
    builder.sidebar_search_entry.set_margin_end(6)
    builder.sidebar_search_entry.set_margin_bottom(12)
    search_container.append(builder.sidebar_search_entry)
    normal_view.append(search_container)

    return normal_view


def build_sidebar(builder: "WindowUIBuilder") -> Gtk.Widget:
    """Build the complete sidebar widget.

    The sidebar is a stack with two pages: the default "normal" view
    (tree + search) and a "context-menu" placeholder that gets
    populated on demand from the tree's right-click handler.
    """
    sidebar_box = Gtk.Box(
        orientation=Gtk.Orientation.VERTICAL, css_classes=["sidebar-container"]
    )

    builder.sidebar_main_stack = Gtk.Stack()
    builder.sidebar_main_stack.set_transition_type(
        Gtk.StackTransitionType.SLIDE_LEFT_RIGHT
    )
    builder.sidebar_main_stack.set_transition_duration(150)
    builder.sidebar_main_stack.set_vexpand(True)

    builder.sidebar_main_stack.add_named(_build_normal_view(builder), "normal")

    # Inline context menu placeholder — the tree populates it the
    # first time a right-click request arrives.
    builder.inline_context_menu_box = Gtk.Box(
        orientation=Gtk.Orientation.VERTICAL, spacing=0, vexpand=True
    )
    builder.inline_context_menu_box.add_css_class("inline-context-menu")
    builder.sidebar_main_stack.add_named(
        builder.inline_context_menu_box, "context-menu"
    )

    sidebar_box.append(builder.sidebar_main_stack)

    # Legacy alias: the rest of the window still refers to
    # ``sidebar_content_stack``.
    builder.sidebar_content_stack = builder.sidebar_main_stack

    return sidebar_box
