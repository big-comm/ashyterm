# ashyterm/ui/dialogs/command_guide_dialog.py

from typing import List, Optional

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, GLib, GObject, Gtk, Pango

from ...data.commands import CommandItem, get_command_manager
from ...utils.translation_utils import _


class CommandEditDialog(Adw.Window):
    """Dialog for adding or editing a custom command."""

    __gsignals__ = {
        "save-requested": (
            GObject.SignalFlags.RUN_FIRST,
            None,
            (GObject.TYPE_PYOBJECT, GObject.TYPE_PYOBJECT),
        ),
    }

    def __init__(self, parent, original_command: Optional[CommandItem] = None):
        super().__init__(
            transient_for=parent, modal=True, default_width=500, default_height=400
        )
        self.original_command = original_command
        is_new = original_command is None
        self.set_title(_("Add Custom Command") if is_new else _("Edit Custom Command"))

        self.name_entry = Adw.EntryRow(title=_("Command"))
        self.category_entry = Adw.EntryRow(title=_("Category"))
        self.description_view = Gtk.TextView(
            wrap_mode=Gtk.WrapMode.WORD_CHAR, vexpand=True
        )

        if not is_new:
            self.name_entry.set_text(original_command.name)
            self.category_entry.set_text(original_command.category)
            self.description_view.get_buffer().set_text(original_command.description)

        self._build_ui()

    def _build_ui(self):
        header = Adw.HeaderBar()
        save_button = Gtk.Button(label=_("Save"), css_classes=["suggested-action"])
        save_button.connect("clicked", self._on_save)
        header.pack_end(save_button)

        page = Adw.PreferencesPage()
        group = Adw.PreferencesGroup()
        page.add(group)
        group.add(self.name_entry)
        group.add(self.category_entry)

        desc_row = Adw.PreferencesRow(title=_("Description"))
        scrolled_desc = Gtk.ScrolledWindow(min_content_height=100)
        scrolled_desc.set_child(self.description_view)
        group.add(desc_row)

        toolbar_view = Adw.ToolbarView()
        toolbar_view.add_top_bar(header)
        toolbar_view.set_content(page)
        self.set_content(toolbar_view)

    def _on_save(self, _button):
        name = self.name_entry.get_text().strip()
        category = self.category_entry.get_text().strip()
        buffer = self.description_view.get_buffer()
        description = buffer.get_text(
            buffer.get_start_iter(), buffer.get_end_iter(), True
        ).strip()

        if not name or not description:
            return

        new_command = CommandItem(name, category, description, is_custom=True)
        self.emit("save-requested", self.original_command, new_command)
        self.close()


class CommandRow(Gtk.ListBoxRow):
    """Custom widget for displaying a command in the ListBox."""

    def __init__(self, command: CommandItem):
        super().__init__()
        self.command = command

        card = Gtk.Box(css_classes=["command-guide-card"], valign=Gtk.Align.CENTER)
        self.set_child(card)

        if command.is_general_description:
            self.set_activatable(False)
            self.set_selectable(False)
            self.set_focusable(False)
            desc_label = Gtk.Label(
                xalign=0.0, wrap=True, wrap_mode=Pango.WrapMode.WORD_CHAR
            )
            desc_label.set_markup(
                f"ℹ️ <b>{GLib.markup_escape_text(command.name)}</b>: {GLib.markup_escape_text(command.description)}"
            )
            desc_label.add_css_class("general-description")
            card.append(desc_label)
        else:
            self.set_activatable(True)
            box = Gtk.Box(
                orientation=Gtk.Orientation.HORIZONTAL, spacing=28, hexpand=True
            )
            card.append(box)

            name_label = Gtk.Label(xalign=0.0, use_markup=True)
            name_label.set_markup(f"<tt>{GLib.markup_escape_text(command.name)}</tt>")
            name_label.set_ellipsize(Pango.EllipsizeMode.END)
            name_label.set_width_chars(35)
            name_label.set_hexpand(False)
            name_label.add_css_class("command-name")

            desc_label = Gtk.Label(
                xalign=0.0, wrap=True, wrap_mode=Pango.WrapMode.WORD_CHAR, hexpand=True
            )
            desc_label.set_text(command.description)
            desc_label.add_css_class("command-description")

            box.append(name_label)
            box.append(desc_label)


class CommandGuideDialog(Adw.Window):
    """Dialog to display a searchable list of useful commands."""

    __gsignals__ = {
        "command-selected": (GObject.SignalFlags.RUN_FIRST, None, (str,)),
    }

    def __init__(self, parent_window):
        super().__init__(transient_for=parent_window, modal=False)
        self.command_manager = get_command_manager()
        self.all_commands: List[CommandItem] = []
        self.category_index = {}  # Track category alternation

        parent_width = parent_window.get_width()
        parent_height = parent_window.get_height()
        self.set_default_size(int(parent_width * 0.8), int(parent_height * 0.9))
        self.set_title(_("Command Guide"))

        self.connect("notify::is-active", self._on_active_changed)

        self._build_ui()
        self._populate_list()
        self._set_initial_selection()

        key_controller = Gtk.EventControllerKey.new()
        key_controller.connect("key-pressed", self._on_window_key_pressed)
        self.add_controller(key_controller)

    def _build_ui(self):
        css_provider = Gtk.CssProvider()
        css_provider.load_from_data(
            b"""
            .command-guide-card {
                background-color: @theme_bg_color;
                border-radius: 10px;
                padding: 16px;
                margin: 6px 16px 6px 16px;
                transition: all 0.2s ease;
                border: 2px solid @borders;
                box-shadow: 0 2px 4px rgba(0, 0, 0, 0.1);
            }
            .command-guide-card:hover {
                background-color: @theme_selected_bg_color;
                border-color: @theme_selected_bg_color;
                box-shadow: 0 4px 8px rgba(0, 0, 0, 0.15);
                transform: translateY(-1px);
            }
            .command-guide-card.selected {
                background-color: alpha(@theme_selected_bg_color, 0.15);
                border-color: alpha(@theme_selected_bg_color, 0.3);
                box-shadow: 0 0 0 2px alpha(@theme_selected_bg_color, 0.2);
            }
            .category-header {
                background-color: @theme_selected_bg_color;
                border-radius: 12px;
                padding: 16px 20px;
                margin: 24px 12px 12px 12px;
                border: 2px solid @borders;
                font-weight: bold;
                font-size: 1.3em;
                color: @theme_selected_fg_color;
                box-shadow: 0 4px 12px rgba(0, 0, 0, 0.15);
            }
            .category-separator {
                background-color: @theme_selected_bg_color;
                margin: 12px 20px;
                min-height: 3px;
                border-radius: 2px;
            }
            .general-description {
                background-color: alpha(@theme_bg_color, 0.8);
                border-radius: 10px;
                padding: 12px 16px;
                margin: 8px 16px;
                border-left: 4px solid @theme_selected_bg_color;
                color: @theme_fg_color;
                font-style: italic;
                box-shadow: 0 2px 6px rgba(0, 0, 0, 0.1);
            }
            .boxed-list {
                background-color: @theme_bg_color;
                border-radius: 8px;
                padding: 12px;
            }
            .command-name {
                font-weight: 700;
                color: @theme_fg_color;
                font-family: 'Monospace';
                font-size: 1.1em;
            }
            .command-description {
                color: alpha(@theme_fg_color, 0.8);
                margin-left: 12px;
                font-size: 0.95em;
                line-height: 1.4;
            }
            .category-even {
                background-color: alpha(@theme_bg_color, 0.95);
            }
            .category-odd {
                background-color: alpha(@theme_selected_bg_color, 0.05);
            }
            .category-icon {
                font-size: 1.4em;
                color: @theme_selected_fg_color;
            }
            .category-title {
                font-size: 1.3em;
                font-weight: bold;
                color: @theme_selected_fg_color;
            }
            """
        )
        Gtk.StyleContext.add_provider_for_display(
            self.get_display(), css_provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )

        toolbar_view = Adw.ToolbarView()
        self.toolbar_view = toolbar_view
        self.set_content(toolbar_view)

        header = Adw.HeaderBar()
        header.set_show_end_title_buttons(False)
        toolbar_view.add_top_bar(header)

        self.search_entry = Gtk.SearchEntry(
            hexpand=True, placeholder_text=_("Search commands...")
        )
        self.search_entry.connect("search-changed", lambda *_: self._filter_list())
        self.search_entry.connect("activate", self._on_search_activate)

        search_key_controller = Gtk.EventControllerKey.new()
        search_key_controller.connect("key-pressed", self._on_search_key_pressed)
        self.search_entry.add_controller(search_key_controller)

        header.set_title_widget(self.search_entry)

        scrolled_window = Gtk.ScrolledWindow(
            vexpand=True, hscrollbar_policy=Gtk.PolicyType.NEVER
        )
        self.scrolled_window = scrolled_window
        toolbar_view.set_content(scrolled_window)

        self.list_box = Gtk.ListBox(
            selection_mode=Gtk.SelectionMode.SINGLE, css_classes=["boxed-list"]
        )
        self.list_box.set_header_func(self._update_header)
        self.list_box.connect("row-activated", self._on_row_activated)
        self.list_box.connect("row-selected", self._on_row_selected)
        scrolled_window.set_child(self.list_box)

        bottom_bar = Adw.HeaderBar()
        bottom_bar.set_show_end_title_buttons(False)
        toolbar_view.add_bottom_bar(bottom_bar)

        self.custom_only_switch = Gtk.Switch(valign=Gtk.Align.CENTER)
        self.custom_only_switch.connect(
            "notify::active", lambda *_: self._filter_list()
        )

        switch_box = Gtk.Box(spacing=6)
        switch_box.append(
            Gtk.Label(label=_("Show Only Custom Commands"), valign=Gtk.Align.CENTER)
        )
        switch_box.append(self.custom_only_switch)
        bottom_bar.pack_start(switch_box)

        actions_box = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL, css_classes=["linked"]
        )
        add_button = Gtk.Button(label=_("Add New"))
        add_button.connect("clicked", self._on_add_clicked)
        self.remove_button = Gtk.Button(
            label=_("Remove Selected"), css_classes=["destructive-action"]
        )
        self.remove_button.connect("clicked", self._on_remove_clicked)
        actions_box.append(add_button)
        actions_box.append(self.remove_button)
        bottom_bar.pack_end(actions_box)

    def _populate_list(self):
        self.all_commands = self.command_manager.get_all_commands()
        self.category_index = {}  # Reset category tracking
        self._filter_list()

    def _filter_list(self):
        while child := self.list_box.get_first_child():
            self.list_box.remove(child)

        search_term = self.search_entry.get_text().lower()
        custom_only = self.custom_only_switch.get_active()

        # Reset category index for fresh assignment
        self.category_index = {}
        category_counter = 0

        matching_base_commands = set()
        if search_term:
            for command in self.all_commands:
                if not command.is_general_description and (
                    search_term in command.name.lower()
                    or search_term in command.description.lower()
                ):
                    base_cmd_item = next(
                        (
                            c
                            for c in self.all_commands
                            if c.is_general_description
                            and c.category == command.category
                            and command.name.startswith(c.name.split()[0])
                        ),
                        None,
                    )
                    if base_cmd_item:
                        matching_base_commands.add(base_cmd_item.name)

        current_category = None
        for command in self.all_commands:
            if custom_only and not command.is_custom:
                continue

            show = False
            if not search_term:
                show = True
            else:
                is_match = (
                    search_term in command.name.lower()
                    or search_term in command.description.lower()
                    or search_term in command.category.lower()
                )
                if command.is_general_description:
                    show = is_match or command.name in matching_base_commands
                else:
                    show = is_match

            if show:
                row = CommandRow(command)

                # Assign alternating background based on category
                if command.category != current_category:
                    current_category = command.category
                    if command.category not in self.category_index:
                        self.category_index[command.category] = category_counter % 2
                        category_counter += 1

                # Apply category-based background
                if self.category_index.get(command.category, 0) % 2 == 0:
                    row.add_css_class("category-even")
                else:
                    row.add_css_class("category-odd")

                self.list_box.append(row)

        self.list_box.invalidate_headers()
        self._update_remove_button_state()

    def _update_header(self, row: CommandRow, before: Optional[CommandRow]):
        if row.get_header() is None:
            command_item = row.command

            # Create enhanced header with icon and better styling
            header_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
            header_box.add_css_class("category-header")

            # Add category icon
            icon_label = Gtk.Label(label="📁", css_classes=["category-icon"])
            icon_label.set_tooltip_text(_("Category"))
            header_box.append(icon_label)

            header_label = Gtk.Label(
                xalign=0.0, use_markup=True, css_classes=["category-title"]
            )
            header_label.set_markup(
                f"<b>{GLib.markup_escape_text(command_item.category)}</b>"
            )
            header_box.append(header_label)

            separator = Gtk.Separator(
                orientation=Gtk.Orientation.HORIZONTAL,
                css_classes=["category-separator"],
            )

            container = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
            container.append(separator)
            container.append(header_box)
            row.set_header(container)

        prev_row = row.get_prev_sibling()
        if prev_row:
            prev_command = prev_row.command
            current_command = row.command
            if prev_command.category == current_command.category:
                if row.get_header():
                    row.get_header().set_visible(False)
            elif row.get_header():
                row.get_header().set_visible(True)
        elif row.get_header():
            row.get_header().set_visible(True)

    def _on_row_selected(self, list_box, row):
        """Handle row selection changes."""
        self._update_selection_styling(row)
        self._update_remove_button_state()

    def _on_row_activated(self, _list_box, row: CommandRow):
        if not row.command.is_general_description:
            command_item = row.command
            self.emit("command-selected", command_item.name)
            self.close()

    def _on_search_activate(self, entry):
        selected = self.list_box.get_selected_row()
        if (
            selected
            and selected.get_selectable()
            and not selected.command.is_general_description
        ):
            self._on_row_activated(self.list_box, selected)
        else:
            child = self.list_box.get_first_child()
            while child:
                if (
                    isinstance(child, CommandRow)
                    and child.get_selectable()
                    and not child.command.is_general_description
                ):
                    self._on_row_activated(self.list_box, child)
                    return
                child = child.get_next_sibling()

    def _on_search_key_pressed(self, controller, keyval, keycode, state):
        if keyval == Gdk.KEY_Down:
            selected = self.list_box.get_selected_row()
            if selected:
                next_row = selected.get_next_sibling()
                while next_row and (
                    not (
                        hasattr(next_row, "get_selectable")
                        and next_row.get_selectable()
                    )
                    or (
                        hasattr(next_row, "command")
                        and next_row.command.is_general_description
                    )
                ):
                    next_row = next_row.get_next_sibling()
                if next_row and hasattr(next_row, "get_selectable"):
                    self.list_box.select_row(next_row)
                    self._update_selection_styling(next_row)
                    self._scroll_to_row(next_row)
            else:
                child = self.list_box.get_first_child()
                while child and (
                    not (hasattr(child, "get_selectable") and child.get_selectable())
                    or (
                        hasattr(child, "command")
                        and child.command.is_general_description
                    )
                ):
                    child = child.get_next_sibling()
                if child and hasattr(child, "get_selectable"):
                    self.list_box.select_row(child)
                    self._update_selection_styling(child)
                    self._scroll_to_row(child)
            return Gdk.EVENT_STOP
        elif keyval == Gdk.KEY_Up:
            selected = self.list_box.get_selected_row()
            if selected:
                prev_row = selected.get_prev_sibling()
                while prev_row and (
                    not (
                        hasattr(prev_row, "get_selectable")
                        and prev_row.get_selectable()
                    )
                    or (
                        hasattr(prev_row, "command")
                        and prev_row.command.is_general_description
                    )
                ):
                    prev_row = prev_row.get_prev_sibling()
                if prev_row and hasattr(prev_row, "get_selectable"):
                    self.list_box.select_row(prev_row)
                    self._update_selection_styling(prev_row)
                    self._scroll_to_row(prev_row)
            else:
                child = self.list_box.get_last_child()
                while child and (
                    not (hasattr(child, "get_selectable") and child.get_selectable())
                    or (
                        hasattr(child, "command")
                        and child.command.is_general_description
                    )
                ):
                    child = child.get_prev_sibling()
                if child and hasattr(child, "get_selectable"):
                    self.list_box.select_row(child)
                    self._update_selection_styling(child)
                    self._scroll_to_row(child)
            return Gdk.EVENT_STOP
        return Gdk.EVENT_PROPAGATE

    def _on_window_key_pressed(self, controller, keyval, keycode, state):
        if keyval == Gdk.KEY_Escape:
            self.close()
            return Gdk.EVENT_STOP

        if self.search_entry.has_focus():
            return Gdk.EVENT_PROPAGATE

        if state & (Gdk.ModifierType.CONTROL_MASK | Gdk.ModifierType.ALT_MASK):
            return Gdk.EVENT_PROPAGATE

        unicode_val = Gdk.keyval_to_unicode(keyval)
        if unicode_val != 0:
            char = chr(unicode_val)
            if char.isprintable():
                self.search_entry.grab_focus()
                current_text = self.search_entry.get_text()
                self.search_entry.set_text(current_text + char)
                self.search_entry.set_position(-1)
                return Gdk.EVENT_STOP

        return Gdk.EVENT_PROPAGATE

    def _on_active_changed(self, widget, pspec):
        if not self.is_active() and self.get_visible():
            self.close()

    def _on_add_clicked(self, _button):
        dialog = CommandEditDialog(self)
        dialog.connect("save-requested", self._on_save_new)
        dialog.present()

    def _on_remove_clicked(self, _button):
        selected_row = self.list_box.get_selected_row()
        if selected_row:
            command_item = selected_row.command
            if command_item.is_custom:
                self.command_manager.remove_custom_command(command_item)
                self._populate_list()

    def _on_save_new(self, _dialog, _original, new_command):
        self.command_manager.add_custom_command(new_command)
        self._populate_list()

    def _scroll_to_row(self, row):
        """Scroll the scrolled window to make the selected row visible."""
        if not row:
            return

        # Use GTK4's proper scrolling method
        vadjustment = self.scrolled_window.get_vadjustment()
        if vadjustment:
            # Get the row's allocation
            allocation = row.get_allocation()

            # Calculate the position to scroll to
            row_top = allocation.y
            row_bottom = allocation.y + allocation.height
            visible_top = vadjustment.get_value()
            visible_bottom = visible_top + vadjustment.get_page_size()

            # If the row is not fully visible, scroll to make it visible
            if row_top < visible_top:
                # Row is above visible area, scroll up to show the row at the top
                vadjustment.set_value(row_top)
            elif row_bottom > visible_bottom:
                # Row is below visible area, scroll down to show the row at the bottom
                vadjustment.set_value(row_bottom - vadjustment.get_page_size())

    def _set_initial_selection(self):
        """Set initial focus to search entry for keyboard navigation."""
        # Set focus to the search entry so keyboard navigation works
        self.search_entry.grab_focus()

    def _update_selection_styling(self, selected_row):
        """Update the visual styling for the selected row."""
        # Remove selected class from all rows first
        child = self.list_box.get_first_child()
        while child:
            if hasattr(child, "get_child"):
                card = child.get_child()
                if card and hasattr(card, "remove_css_class"):
                    card.remove_css_class("selected")
            child = child.get_next_sibling()

        # Add selected class to the newly selected row
        if selected_row and hasattr(selected_row, "get_child"):
            card = selected_row.get_child()
            if card and hasattr(card, "add_css_class"):
                card.add_css_class("selected")

    def _update_remove_button_state(self):
        """Update the sensitivity of the remove button based on the selected row."""
        selected_row = self.list_box.get_selected_row()
        is_custom = selected_row and selected_row.command.is_custom
        self.remove_button.set_sensitive(is_custom)
