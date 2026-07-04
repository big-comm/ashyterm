# ashyterm/ui/header_bar_builder.py
"""Header-bar button construction + layout.

The window header shows eight buttons (sidebar toggle, file manager,
command manager, search, AI assistant, cleanup menu, main menu, new
tab). Building them is 80 lines of widget boilerplate plus a ~20-line
left-controls packing dance — keeping it in its own module lets
``window_ui`` stay focused on wiring callbacks and lifecycles.

Each helper here receives the owning ``WindowUIBuilder`` and stores
the widgets back on it (``builder.toggle_sidebar_button`` …). That
preserves the existing callsites in ``WindowUIBuilder`` without
forcing a wider API change.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk, Pango

from ..utils.accessibility import set_label as a11y_label
from ..utils.icons import icon_button, icon_image
from ..utils.translation_utils import _

if TYPE_CHECKING:
    from .window_ui import WindowUIBuilder


# Widgets whose CSS class must flip when window controls are on the
# left (KDE/Plasma default). The class ``flipped-icon`` is defined in
# the shared stylesheet and mirrors the glyph horizontally.
_FLIPPABLE_ATTR_NAMES: tuple[str, ...] = (
    "toggle_sidebar_button",
    "file_manager_button",
    "command_manager_button",
    "search_button",
    "ai_assistant_button",
    "cleanup_button",
    "menu_button",
    "new_tab_button",
)


def _window_controls_on_left(button_layout: str) -> bool:
    """Return True if the GNOME ``button-layout`` puts close/min/max on the left.

    The setting is encoded as ``"left:right"`` (e.g.
    ``"close:minimize,maximize"``). We look at the ``left`` half for
    any of the three control names.
    """
    if ":" not in button_layout:
        return False
    left_part = button_layout.split(":")[0]
    return any(btn in left_part for btn in ("close", "minimize", "maximize"))


def _create_buttons(builder: "WindowUIBuilder") -> None:
    """Instantiate every header-bar button onto ``builder.*``.

    Side effects: also registers tooltips via ``builder.tooltip_helper``
    and wires the two buttons whose click handler is a one-liner (AI
    assistant, new tab) — everything else is either bound via
    ``set_action_name`` or wired from the calling window.
    """
    # Toggle buttons come first.
    # NOTE: ".flat" is explicit on every header button: newer libadwaita
    # (>= 1.6) renders header-bar buttons flat by default, but older stacks
    # (e.g. libadwaita 1.5 in AppImage builds) draw a raised background
    # ("squares" around the icons) when the class is missing.
    builder.toggle_sidebar_button = Gtk.ToggleButton()
    builder.toggle_sidebar_button.set_child(icon_image("user-bookmarks-symbolic"))
    builder.toggle_sidebar_button.add_css_class("sidebar-toggle-button")
    builder.toggle_sidebar_button.add_css_class("flat")
    a11y_label(builder.toggle_sidebar_button, _("Sessions Panel"))

    builder.file_manager_button = Gtk.ToggleButton()
    builder.file_manager_button.set_child(icon_image("folder-open-symbolic"))
    builder.file_manager_button.add_css_class("flat")
    a11y_label(builder.file_manager_button, _("File Manager"))

    builder.command_manager_button = Gtk.Button()
    builder.command_manager_button.set_child(
        icon_image("utilities-terminal-symbolic")
    )
    builder.command_manager_button.set_action_name("win.show-command-manager")
    builder.command_manager_button.add_css_class("flat")
    a11y_label(builder.command_manager_button, _("Command Manager"))

    builder.search_button = Gtk.ToggleButton()
    builder.search_button.set_child(icon_image("edit-find-symbolic"))
    builder.search_button.add_css_class("flat")
    a11y_label(builder.search_button, _("Search in Terminal"))

    # Broadcast button stays hidden — the functionality is embedded in
    # the Command Manager now, but we keep the widget around for the
    # handful of code paths that reference it.
    builder.broadcast_button = Gtk.ToggleButton()
    builder.broadcast_button.set_child(icon_image("utilities-terminal-symbolic"))
    builder.broadcast_button.add_css_class("flat")
    builder.broadcast_button.set_visible(False)

    # AI assistant button: system icon + runtime visibility toggle.
    builder.ai_assistant_button = Gtk.Button()
    builder.ai_assistant_button.set_child(
        icon_image("avatar-default-symbolic", use_bundled=False)
    )
    builder.ai_assistant_button.add_css_class("flat")
    a11y_label(builder.ai_assistant_button, _("Ask AI Assistant"))
    builder.ai_assistant_button.connect(
        "clicked", lambda _btn: builder.window._on_ai_assistant_requested()
    )
    ai_enabled = builder.settings_manager.get("ai_assistant_enabled", False)
    builder.ai_assistant_button.set_visible(ai_enabled)

    # Cleanup (temp files) — opens a popover populated later.
    builder.cleanup_button = Gtk.MenuButton(visible=False)
    builder.cleanup_button.set_child(icon_image("user-trash-symbolic"))
    builder.cleanup_button.add_css_class("destructive-action")
    builder.cleanup_button.add_css_class("flat")
    a11y_label(builder.cleanup_button, _("Manage Temporary Files"))
    builder.cleanup_popover = Gtk.Popover()
    builder.cleanup_popover.add_css_class("ashyterm-popover")
    builder.cleanup_button.set_popover(builder.cleanup_popover)
    builder.cleanup_popover.connect(
        "show", lambda _p: builder.tooltip_helper.hide()
    )

    # Main menu — populated lazily the first time the user opens it.
    builder.menu_button = Gtk.MenuButton()
    builder.menu_button.set_child(icon_image("open-menu-symbolic"))
    builder.menu_button.add_css_class("flat")
    a11y_label(builder.menu_button, _("Main Menu"))
    builder._main_menu_popover = None
    builder._setup_lazy_menu_popover()

    builder.new_tab_button = icon_button("tab-new-symbolic")
    builder.new_tab_button.connect("clicked", builder.window._on_new_tab_clicked)
    builder.new_tab_button.add_css_class("flat")
    a11y_label(builder.new_tab_button, _("New Tab"))


def _create_tab_list_button(builder: "WindowUIBuilder") -> Gtk.MenuButton:
    """Dropdown at the end of the tab strip listing every open tab.

    With many tabs open the strip scrolls and it becomes hard to know
    what is running where. The popover lists all tabs — session icon,
    title, Alt+N shortcut and a marker on the active one — and jumps
    to the activated tab. It is rebuilt on every open so it is always
    current, with no bookkeeping when tabs change.
    """
    button = Gtk.MenuButton()
    button.set_child(icon_image("pan-down-symbolic"))
    button.add_css_class("flat")
    button.set_valign(Gtk.Align.CENTER)
    a11y_label(button, _("Open Tabs"))

    popover = Gtk.Popover()
    popover.add_css_class("ashyterm-popover")
    button.set_popover(popover)

    def _on_row_activated(_listbox, row):
        popover.popdown()
        tab = row._tab_widget
        # The tab may have been closed while the popover was open.
        if tab in builder.tab_manager.tabs:
            builder.tab_manager.set_active_tab(tab)

    def _rebuild(_popover):
        manager = builder.tab_manager
        listbox = Gtk.ListBox()
        listbox.set_selection_mode(Gtk.SelectionMode.NONE)
        listbox.connect("row-activated", _on_row_activated)

        for index, tab in enumerate(manager.tabs):
            row_box = Gtk.Box(
                orientation=Gtk.Orientation.HORIZONTAL,
                spacing=8,
                margin_start=8,
                margin_end=8,
                margin_top=4,
                margin_bottom=4,
            )
            icon_name = (
                "computer-symbolic"
                if getattr(tab, "_is_local", True)
                else "network-server-symbolic"
            )
            row_box.append(icon_image(icon_name))

            label_widget = getattr(tab, "label_widget", None)
            title = (
                label_widget.get_text()
                if label_widget is not None
                else getattr(tab, "_base_title", _("Terminal"))
            )
            title_label = Gtk.Label(
                label=title,
                xalign=0,
                hexpand=True,
                ellipsize=Pango.EllipsizeMode.END,
                max_width_chars=40,
            )
            row_box.append(title_label)

            if tab is manager.active_tab:
                row_box.append(icon_image("object-select-symbolic"))

            # Alt+1..9 then Alt+0 switch to the first ten tabs
            # (see window_actions._handle_alt_number_shortcuts).
            if index < 10:
                accel_label = Gtk.Label(label=f"Alt+{(index + 1) % 10}")
                accel_label.add_css_class("dim-label")
                row_box.append(accel_label)

            row = Gtk.ListBoxRow()
            row.set_child(row_box)
            row._tab_widget = tab
            listbox.append(row)

        scrolled = Gtk.ScrolledWindow(
            propagate_natural_height=True,
            propagate_natural_width=True,
            max_content_height=420,
        )
        scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scrolled.set_child(listbox)
        popover.set_child(scrolled)

    popover.connect("show", _rebuild)
    # Exposed for tests and for callers that need a manual refresh.
    button._rebuild_tab_list = _rebuild
    return button


def _attach_tooltips(builder: "WindowUIBuilder") -> None:
    """Attach custom tooltips to every header-bar button.

    Uses the shared ``tooltip_helper`` singleton so hover-timing, dark
    mode, and accessibility behave consistently across the window.
    """
    helper = builder.tooltip_helper
    helper.add_tooltip(builder.toggle_sidebar_button, _("Sessions Panel"))
    helper.add_tooltip(builder.file_manager_button, _("File Manager"))
    helper.add_tooltip(builder.command_manager_button, _("Command Manager"))
    helper.add_tooltip(builder.search_button, _("Search in Terminal"))
    helper.add_tooltip(builder.ai_assistant_button, _("Ask AI Assistant"))
    helper.add_tooltip(builder.cleanup_button, _("Manage Temporary Files"))
    helper.add_tooltip(builder.menu_button, _("Main Menu"))
    helper.add_tooltip(builder.new_tab_button, _("New Tab"))
    helper.add_tooltip(builder.tab_list_button, _("Open Tabs"))


def _pack_buttons(
    header_bar: Adw.HeaderBar,
    builder: "WindowUIBuilder",
    *,
    flipped: bool,
) -> None:
    """Pack the header-bar buttons respecting the window-controls side.

    When window controls live on the left (KDE default), the header
    mirrors horizontally: action buttons pack to the right end, new
    tab / menu go to the start end, and each button gets a
    ``flipped-icon`` CSS class so asymmetric glyphs read correctly.
    """
    if flipped:
        for name in _FLIPPABLE_ATTR_NAMES:
            getattr(builder, name).add_css_class("flipped-icon")

        # Left-controls layout: reversed button ordering.
        header_bar.pack_end(builder.toggle_sidebar_button)
        header_bar.pack_end(builder.file_manager_button)
        header_bar.pack_end(builder.command_manager_button)
        header_bar.pack_end(builder.ai_assistant_button)
        header_bar.pack_end(builder.search_button)
        header_bar.pack_end(builder.cleanup_button)
        header_bar.pack_start(builder.menu_button)
        header_bar.pack_start(builder.new_tab_button)
        return

    header_bar.pack_start(builder.toggle_sidebar_button)
    header_bar.pack_start(builder.file_manager_button)
    header_bar.pack_start(builder.command_manager_button)
    header_bar.pack_start(builder.ai_assistant_button)
    header_bar.pack_start(builder.search_button)
    header_bar.pack_start(builder.cleanup_button)
    header_bar.pack_end(builder.menu_button)
    header_bar.pack_end(builder.new_tab_button)


def build_header_bar(builder: "WindowUIBuilder") -> Adw.HeaderBar:
    """Build the window's ``Adw.HeaderBar`` from scratch.

    Creates every button, wires tooltips, assembles the tab-bar /
    single-title stack, and respects the window-controls side. Returns
    the header ready to be packed into the main window structure.
    """
    header_bar = Adw.HeaderBar(css_classes=["main-header-bar"])
    # Published eagerly so menus loaded during button creation can
    # reach back into ``window.header_bar`` without waiting for the
    # return value.
    builder.window.header_bar = header_bar

    _create_buttons(builder)
    builder.tab_list_button = _create_tab_list_button(builder)
    _attach_tooltips(builder)

    button_layout = (
        builder.wm_settings.get_string("button-layout")
        if builder.wm_settings
        else ""
    )
    _pack_buttons(
        header_bar, builder, flipped=_window_controls_on_left(button_layout)
    )

    # Tab-bar scrolled container + single-tab title widget live in a
    # stack so the header can swap between them without re-adding.
    builder.scrolled_tab_bar = Gtk.ScrolledWindow(
        name="scrolled_tab_bar",
        propagate_natural_height=True,
        hexpand=True,
    )
    builder.scrolled_tab_bar.add_css_class("scrolled-tab-bar")
    builder.scrolled_tab_bar.set_policy(
        Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.NEVER
    )
    builder.scrolled_tab_bar.set_child(builder.tab_manager.get_tab_bar())

    scroll_controller = Gtk.EventControllerScroll.new(
        Gtk.EventControllerScrollFlags.BOTH_AXES
    )
    scroll_controller.connect("scroll", builder.window._on_tab_bar_scroll)
    builder.scrolled_tab_bar.add_controller(scroll_controller)

    builder.single_tab_title_widget = Adw.WindowTitle(title=_("Ashy Terminal"))

    # Tab strip + open-tabs dropdown. Lives inside the "tabs-view" stack
    # page, so the dropdown only appears when there is more than one tab.
    tabs_view_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=2)
    tabs_view_box.append(builder.scrolled_tab_bar)
    tabs_view_box.append(builder.tab_list_button)

    builder.title_stack = Gtk.Stack()
    builder.title_stack.add_named(tabs_view_box, "tabs-view")
    builder.title_stack.add_named(builder.single_tab_title_widget, "title-view")
    header_bar.set_title_widget(builder.title_stack)

    return header_bar
