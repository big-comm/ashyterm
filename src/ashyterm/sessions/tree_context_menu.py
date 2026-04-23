# ashyterm/sessions/tree_context_menu.py
"""Right-click menus for the sessions tree view.

Two presentations:

* **Inline** (shown inside the sidebar popover) — a swappable widget
  rendered via :class:`InlineContextMenu`, used when the sidebar is in
  popover mode so the menu doesn't escape the popover.
* **Popover** (traditional ``Gtk.PopoverMenu``) — used when the
  sidebar is docked and there's screen space to host a real popover.

Each of the four entry points — item/root × inline/popover — used to
live on ``SessionTreeView``. Extracting them keeps that class focused
on selection/filter/model orchestration.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Union

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("Graphene", "1.0")
from gi.repository import Gdk, Gio, Graphene, Gtk

from ..helpers import clear_children, create_themed_popover_menu
from ..utils.translation_utils import _
from .models import LayoutItem, SessionFolder, SessionItem

if TYPE_CHECKING:
    from .tree import SessionTreeView


def _resolve_sidebar_slots(view: "SessionTreeView"):
    """Return ``(content_stack, inline_menu_box)`` if the sidebar is ready.

    Both attributes are created by ``WindowUIBuilder.build_sidebar``; if
    the window hasn't finished building (or we're mid-teardown), one
    of them may be missing — return ``(None, None)`` in that case so
    callers short-circuit cleanly.
    """
    ui_builder = getattr(view.parent_window, "ui_builder", None)
    if ui_builder is None:
        return None, None
    content_stack = getattr(ui_builder, "sidebar_content_stack", None)
    inline_menu_box = getattr(ui_builder, "inline_context_menu_box", None)
    if not content_stack or not inline_menu_box:
        return None, None
    return content_stack, inline_menu_box


def _build_inline_menu(view: "SessionTreeView", content_stack: Gtk.Stack):
    """Instantiate an ``InlineContextMenu`` wired to swap the stack back."""
    from ..ui.widgets.inline_context_menu import InlineContextMenu

    menu = InlineContextMenu(view.parent_window)
    menu.set_go_back_callback(
        lambda: content_stack.set_visible_child_name("normal")
    )
    return menu


def _rect_from_anchor(
    anchor: Gtk.Widget, parent_window: Gtk.Widget, x: float, y: float
) -> Gdk.Rectangle:
    """Translate (x,y) inside ``anchor`` to window coordinates.

    ``compute_point`` returns (False, ...) when the widget isn't yet
    realized; in that case we fall back to the raw (x, y) which is
    good enough — the popover will just render a few pixels off.
    """
    point = Graphene.Point()
    point.x = x
    point.y = y

    rect = Gdk.Rectangle()
    success, translated = anchor.compute_point(parent_window, point)
    if success:
        rect.x = int(translated.x)
        rect.y = int(translated.y)
    else:
        rect.x = int(x)
        rect.y = int(y)
    rect.width = 1
    rect.height = 1
    return rect


def _build_layout_menu(item: LayoutItem) -> Gio.Menu:
    """Build the popover menu for a saved tab layout."""
    menu = Gio.Menu()
    menu.append(_("Restore Layout"), f"win.restore_layout('{item.name}')")
    menu.append(
        _("Move to Folder..."), f"win.move-layout-to-folder('{item.name}')"
    )
    menu.append_section(None, Gio.Menu())
    menu.append(_("Delete Layout"), f"win.delete_layout('{item.name}')")
    return menu


# ── public entry points ────────────────────────────────────


def show_inline_context_menu(
    view: "SessionTreeView",
    item: Union[SessionItem, SessionFolder, LayoutItem],
) -> None:
    """Swap the sidebar to an inline context menu tailored to ``item``."""
    content_stack, inline_menu_box = _resolve_sidebar_slots(view)
    if content_stack is None:
        return

    clear_children(inline_menu_box)
    inline_menu = _build_inline_menu(view, content_stack)

    if isinstance(item, SessionItem):
        inline_menu.show_for_session(
            item, view.folder_store, view.has_clipboard_content()
        )
    elif isinstance(item, SessionFolder):
        inline_menu.show_for_folder(item, view.has_clipboard_content())
    elif isinstance(item, LayoutItem):
        inline_menu.show_for_layout(item)
    else:
        view.logger.debug(f"Unknown item type: {type(item)}")
        return

    inline_menu_box.append(inline_menu)
    content_stack.set_visible_child_name("context-menu")


def show_popover_context_menu(
    view: "SessionTreeView",
    item: Union[SessionItem, SessionFolder, LayoutItem],
    list_item: Gtk.ListItem,
    x: float,
    y: float,
) -> None:
    """Open a traditional ``Gtk.PopoverMenu`` anchored at ``(x, y)``.

    The menu model is chosen based on ``item``'s type. For layout items
    we inline a tiny ``Gio.Menu`` here because they have only three
    actions; sessions/folders pull their model from the lazy menu
    factories in :mod:`ashyterm.ui.menus`.
    """
    # Lazy imports match the pattern used inside SessionTreeView to
    # keep the menus module off the startup path.
    from .tree import (
        _get_create_folder_menu,
        _get_create_session_menu,
    )

    menu_model = None
    if isinstance(item, SessionItem):
        found, position = view.session_store.find(item)
        if found:
            menu_model = _get_create_session_menu()(
                item,
                view.session_store,
                position,
                view.folder_store,
                view.has_clipboard_content(),
            )
    elif isinstance(item, SessionFolder):
        found, position = view.folder_store.find(item)
        if found:
            menu_model = _get_create_folder_menu()(
                item,
                view.folder_store,
                position,
                view.session_store,
                view.has_clipboard_content(),
            )
    elif isinstance(item, LayoutItem):
        menu_model = _build_layout_menu(item)

    if menu_model is None:
        return

    anchor_widget = list_item.get_child()
    popover = create_themed_popover_menu(menu_model, view.parent_window)
    popover.set_pointing_to(
        _rect_from_anchor(anchor_widget, view.parent_window, x, y)
    )
    popover.popup()


def show_inline_root_context_menu(view: "SessionTreeView") -> None:
    """Inline variant for a right-click on empty space (root)."""
    content_stack, inline_menu_box = _resolve_sidebar_slots(view)
    if content_stack is None:
        return

    clear_children(inline_menu_box)
    inline_menu = _build_inline_menu(view, content_stack)
    inline_menu.show_for_root(view.has_clipboard_content())
    inline_menu_box.append(inline_menu)
    content_stack.set_visible_child_name("context-menu")


def show_popover_root_context_menu(
    view: "SessionTreeView", x: float, y: float
) -> None:
    """Popover variant for a right-click on empty space."""
    from .tree import _get_create_root_menu

    menu_model = _get_create_root_menu()(view.has_clipboard_content())
    popover = create_themed_popover_menu(menu_model, view.parent_window)
    popover.set_pointing_to(
        _rect_from_anchor(view.column_view, view.parent_window, x, y)
    )
    popover.popup()
