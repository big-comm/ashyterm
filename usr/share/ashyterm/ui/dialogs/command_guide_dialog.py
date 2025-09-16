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

        self.name_view = Gtk.TextView(wrap_mode=Gtk.WrapMode.WORD_CHAR, vexpand=True)
        self.category_entry = Adw.EntryRow(title=_("Category"))
        self.description_view = Gtk.TextView(
            wrap_mode=Gtk.WrapMode.WORD_CHAR, vexpand=True
        )

        if not is_new:
            self.name_view.get_buffer().set_text(original_command.name)
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

        name_row = Adw.PreferencesRow(title=_("Command"))
        scrolled_name = Gtk.ScrolledWindow(min_content_height=60)
        scrolled_name.set_child(self.name_view)
        name_row.set_child(scrolled_name)
        group.add(name_row)

        group.add(self.category_entry)

        desc_row = Adw.PreferencesRow(title=_("Description"))
        scrolled_desc = Gtk.ScrolledWindow(min_content_height=100)
        scrolled_desc.set_child(self.description_view)
        desc_row.set_child(scrolled_desc)
        group.add(desc_row)

        toolbar_view = Adw.ToolbarView()
        toolbar_view.add_top_bar(header)
        toolbar_view.set_content(page)
        self.set_content(toolbar_view)

    def _on_save(self, _button):
        buffer = self.name_view.get_buffer()
        name = buffer.get_text(
            buffer.get_start_iter(), buffer.get_end_iter(), True
        ).strip()
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
                orientation=Gtk.Orientation.HORIZONTAL, spacing=12, hexpand=True
            )
            card.append(box)

            # Command name
            name_label = Gtk.Label(xalign=0.0, use_markup=True)
            name_label.set_markup(f"<tt>{GLib.markup_escape_text(command.name)}</tt>")
            name_label.set_wrap(True)
            name_label.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
            name_label.set_width_chars(25)
            name_label.set_hexpand(False)
            name_label.add_css_class("command-name")
            box.append(name_label)

            # Command description
            desc_label = Gtk.Label(
                xalign=0.0, wrap=True, wrap_mode=Pango.WrapMode.WORD_CHAR, hexpand=True
            )
            desc_label.set_text(command.description)
            desc_label.add_css_class("command-description")
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

        # Store references to event controllers for cleanup
        self._event_controllers = []

        parent_width = parent_window.get_width()
        parent_height = parent_window.get_height()
        self.set_default_size(int(parent_width * 0.8), int(parent_height * 0.85))
        self.set_title(_("Command Guide"))

        self.connect("notify::is-active", self._on_active_changed)
        self.connect("destroy", self._on_destroy)
        self.connect("close-request", self._on_close_request)

        self._build_ui()
        self._populate_list()
        self._set_initial_selection()

        # Initialize sticky header after UI is built
        GLib.idle_add(self._update_sticky_header)

        key_controller = Gtk.EventControllerKey.new()
        key_controller.connect("key-pressed", self._on_window_key_pressed)
        self.add_controller(key_controller)
        self._event_controllers.append(key_controller)

    def _build_ui(self):
        css_provider = Gtk.CssProvider()
        css_provider.load_from_data(
            b"""
            .command-guide-card {
                border-radius: 6px;
                padding: 0px;
                margin-right: 14px;
                margin-left: 14px;
                transition: all 0.2s ease;
                border: 0px solid alpha(@borders, 0.6);
                box-shadow: 0 1px 2px rgba(0, 0, 0, 0.08);
                background-color: alpha(@theme_bg_color, 0.98);
            }
            .command-guide-card:hover {
                background-color: alpha(@theme_selected_bg_color, 0.08);
                border-color: alpha(@theme_selected_bg_color, 0.4);
                box-shadow: 0 2px 4px rgba(0, 0, 0, 0.12);
                transform: translateY(-1px);
            }
            .command-guide-card.selected {
                background-color: alpha(@theme_selected_bg_color, 0.12);
                border-color: alpha(@theme_selected_bg_color, 0.5);
                box-shadow: 0 0 0 2px alpha(@theme_selected_bg_color, 0.25);
            }
            .category-header {
                background: linear-gradient(135deg, alpha(@theme_selected_bg_color, 0.9) 0%, alpha(@theme_selected_bg_color, 0.7) 100%);
                border-radius: 4px;
                padding: 4px 8px;
                margin-left: 0;
                margin-right: 12px;
                font-weight: bold;
                color: @theme_selected_fg_color;
            }
            .category-separator {
                background: linear-gradient(90deg, @accent_color 0%, @theme_selected_bg_color 50%, @accent_color 100%);
                margin: 0px;
                min-height: 1px;
                border-radius: 1px;
                opacity: 0.8;
            }
            .general-description {
                background: linear-gradient(135deg, alpha(@theme_bg_color, 0.95) 0%, alpha(@theme_bg_color, 0.9) 100%);
                border-radius: 4px;
                padding: 4px 6px;
                margin: 2px 6px;
                border-left: 2px solid @accent_color;
                color: @theme_fg_color;
                font-style: italic;
                box-shadow: inset 0 1px 2px rgba(0, 0, 0, 0.05);
                border: 1px solid alpha(@borders, 0.4);
                font-size: 0.85em;
            }
            .boxed-list {
                background-color: alpha(@theme_bg_color, 0.98);
                border-radius: 4px;
                padding: 3px;
                border: 1px solid alpha(@borders, 0.3);
            }
            .command-name {
                font-weight: 600;
                color: @theme_fg_color;
                font-family: 'Monospace';
                font-size: 0.9em;
                background-color: alpha(@theme_selected_bg_color, 0.1);
                padding: 1px 4px;
                border-radius: 2px;
                border: 1px solid alpha(@theme_selected_bg_color, 0.3);
            }
            .command-description {
                color: @theme_fg_color;
                margin-left: 6px;
                font-size: 1em;
                line-height: 1.4;
                font-weight: 500;
                opacity: 0.95;
            }
            .category-icon {
                font-size: 1em;
                color: @accent_color;
                font-weight: bold;
            }
            .category-title {
                font-size: 1em;
                font-weight: bold;
                color: @theme_selected_fg_color;
                text-shadow: 0 1px 2px rgba(0, 0, 0, 0.2);
            }
            .sticky-header-container {
                background: linear-gradient(180deg, alpha(@theme_bg_color, 0.95) 0%, alpha(@theme_bg_color, 0.9) 100%);
                margin-top: -15px;
                margin-left: 0px;
                padding: 2px;
            }
            .sticky-category-header {
                background: linear-gradient(135deg, alpha(@theme_selected_bg_color, 0.9) 0%, alpha(@theme_selected_bg_color, 0.7) 100%);
                border-radius: 4px;
                padding: 4px 8px;
                margin-top: 0;
                margin-left: -4px;
                margin-right: 12px;
                font-weight: bold;
                color: @theme_selected_fg_color;
            }
            .sticky-general-description {
                border-radius: 3px;
                padding: 3px 5px;
                color: @theme_fg_color;
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
        self._event_controllers.append(search_key_controller)

        header.set_title_widget(self.search_entry)

        scrolled_window = Gtk.ScrolledWindow(
            vexpand=True, hscrollbar_policy=Gtk.PolicyType.NEVER
        )
        self.scrolled_window = scrolled_window

        # Create overlay for sticky headers
        overlay = Gtk.Overlay()
        overlay.set_child(scrolled_window)

        # Create sticky header container
        self.sticky_header = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.sticky_header.add_css_class("sticky-header-container")
        self.sticky_header.set_visible(False)  # Initially hidden
        self.sticky_header.set_valign(Gtk.Align.START)  # Position at top
        self.sticky_header.set_margin_top(
            16
        )  # Increase top margin to avoid search bar overlap
        self.sticky_header.set_margin_start(8)  # Add left margin
        self.sticky_header.set_margin_end(8)  # Add right margin

        # Category header in sticky overlay
        self.sticky_category_box = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL, spacing=6
        )
        self.sticky_category_box.add_css_class("sticky-category-header")
        self.sticky_category_icon = Gtk.Label(label="📁")
        self.sticky_category_icon.add_css_class("category-icon")
        self.sticky_category_label = Gtk.Label(xalign=0.0, use_markup=True)
        self.sticky_category_label.add_css_class("category-title")
        self.sticky_category_box.append(self.sticky_category_icon)
        self.sticky_category_box.append(self.sticky_category_label)
        self.sticky_header.append(self.sticky_category_box)

        # General description in sticky overlay
        self.sticky_description_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        self.sticky_description_box.add_css_class("sticky-general-description")
        self.sticky_description_label = Gtk.Label(
            xalign=0.0, wrap=True, wrap_mode=Pango.WrapMode.WORD_CHAR
        )
        self.sticky_description_label.add_css_class("general-description")
        self.sticky_description_box.append(self.sticky_description_label)
        self.sticky_header.append(self.sticky_description_box)

        # Add sticky header to overlay
        overlay.add_overlay(self.sticky_header)

        self.list_box = Gtk.ListBox(
            selection_mode=Gtk.SelectionMode.SINGLE, css_classes=["boxed-list"]
        )
        self.list_box.set_header_func(self._update_header)
        self.list_box.connect("row-activated", self._on_row_activated)
        self.list_box.connect("row-selected", self._on_row_selected)
        scrolled_window.set_child(self.list_box)

        # Connect scroll event to update sticky header
        vadjustment = scrolled_window.get_vadjustment()
        if vadjustment:
            vadjustment.connect("value-changed", self._on_scroll_changed)

        toolbar_view.set_content(overlay)

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
        self._filter_list()

    def _filter_list(self):
        while child := self.list_box.get_first_child():
            self.list_box.remove(child)

        search_term = self.search_entry.get_text().lower()
        custom_only = self.custom_only_switch.get_active()

        for command in self.all_commands:
            if custom_only and not command.is_custom:
                continue

            # Show command if no search term or if it matches search
            show = not search_term or (
                search_term in command.name.lower()
                or search_term in command.description.lower()
                or search_term in command.category.lower()
            )

            if show:
                row = CommandRow(command)
                self.list_box.append(row)

        self.list_box.invalidate_headers()
        self._update_remove_button_state()
        GLib.idle_add(self._update_sticky_header)

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

            container = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
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
            # Find first selectable command
            first_row = self._find_selectable_row(None, "next")
            if first_row:
                self._on_row_activated(self.list_box, first_row)

    def _find_selectable_row(self, start_row, direction):
        """Find the next or previous selectable row in the given direction."""
        if direction == "next":
            current = (
                start_row.get_next_sibling()
                if start_row
                else self.list_box.get_first_child()
            )
            while current:
                if (
                    hasattr(current, "get_selectable")
                    and current.get_selectable()
                    and hasattr(current, "command")
                    and not current.command.is_general_description
                ):
                    return current
                current = current.get_next_sibling()
        elif direction == "prev":
            current = (
                start_row.get_prev_sibling()
                if start_row
                else self.list_box.get_last_child()
            )
            while current:
                if (
                    hasattr(current, "get_selectable")
                    and current.get_selectable()
                    and hasattr(current, "command")
                    and not current.command.is_general_description
                ):
                    return current
                current = current.get_prev_sibling()
        return None

    def _on_search_key_pressed(self, controller, keyval, keycode, state):
        if keyval == Gdk.KEY_Down:
            selected = self.list_box.get_selected_row()
            next_row = self._find_selectable_row(selected, "next")
            if next_row:
                self.list_box.select_row(next_row)
                self._update_selection_styling(next_row)
                self._scroll_to_row(next_row)
            return Gdk.EVENT_STOP
        elif keyval == Gdk.KEY_Up:
            selected = self.list_box.get_selected_row()
            prev_row = self._find_selectable_row(selected, "prev")
            if prev_row:
                self.list_box.select_row(prev_row)
                self._update_selection_styling(prev_row)
                self._scroll_to_row(prev_row)
            return Gdk.EVENT_STOP
        return Gdk.EVENT_PROPAGATE

    def _on_window_key_pressed(self, controller, keyval, keycode, state):
        if keyval == Gdk.KEY_Escape:
            self.close()
            return Gdk.EVENT_STOP

        if self.search_entry.has_focus() or state & (
            Gdk.ModifierType.CONTROL_MASK | Gdk.ModifierType.ALT_MASK
        ):
            return Gdk.EVENT_PROPAGATE

        # Handle printable characters by focusing search
        unicode_val = Gdk.keyval_to_unicode(keyval)
        if unicode_val and chr(unicode_val).isprintable():
            self.search_entry.grab_focus()
            current_text = self.search_entry.get_text()
            self.search_entry.set_text(current_text + chr(unicode_val))
            self.search_entry.set_position(-1)
            return Gdk.EVENT_STOP

        return Gdk.EVENT_PROPAGATE

    def _on_destroy(self, widget):
        """Clean up resources when dialog is destroyed to prevent memory leaks."""
        self._cleanup_resources()

    def _on_close_request(self, widget):
        """Ensure cleanup happens, then allow the window to close."""
        self.hide()
        return Gdk.EVENT_STOP

    def _cleanup_resources(self):
        """Clean up widgets and resources to free memory."""
        try:
            # Clear all list box children to free widget memory
            while child := self.list_box.get_first_child():
                self.list_box.remove(child)

            # Clear command data references
            self.all_commands.clear()

            # Disconnect scroll event handler
            if hasattr(self, "scrolled_window") and self.scrolled_window:
                vadjustment = self.scrolled_window.get_vadjustment()
                if vadjustment and hasattr(self, "_scroll_handler_id"):
                    if GObject.signal_handler_is_connected(
                        vadjustment, self._scroll_handler_id
                    ):
                        vadjustment.disconnect(self._scroll_handler_id)
                    del self._scroll_handler_id

        except Exception as e:
            # Log error but don't crash during cleanup
            print(f"Warning: Error during CommandGuideDialog cleanup: {e}")

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
        if not row or not self.scrolled_window:
            return

        vadjustment = self.scrolled_window.get_vadjustment()
        if not vadjustment:
            return

        allocation = row.get_allocation()
        row_top = allocation.y
        row_bottom = allocation.y + allocation.height
        visible_top = vadjustment.get_value()
        visible_bottom = visible_top + vadjustment.get_page_size()

        if row_top < visible_top:
            vadjustment.set_value(row_top)
        elif row_bottom > visible_bottom:
            vadjustment.set_value(row_bottom - vadjustment.get_page_size())

    def _set_initial_selection(self):
        """Set initial focus to search entry for keyboard navigation."""
        # Set focus to the search entry so keyboard navigation works
        self.search_entry.grab_focus()

    def _update_selection_styling(self, selected_row):
        """Update the visual styling for the selected row."""
        # Remove selected class from all rows
        child = self.list_box.get_first_child()
        while child:
            if hasattr(child, "get_child"):
                card = child.get_child()
                if card and hasattr(card, "remove_css_class"):
                    card.remove_css_class("selected")
            child = child.get_next_sibling()

        # Add selected class to the new row
        if selected_row and hasattr(selected_row, "get_child"):
            card = selected_row.get_child()
            if card and hasattr(card, "add_css_class"):
                card.add_css_class("selected")

    def _update_remove_button_state(self):
        """Update the remove button state based on selected row."""
        selected_row = self.list_box.get_selected_row()
        self.remove_button.set_sensitive(
            selected_row
            and hasattr(selected_row, "command")
            and selected_row.command.is_custom
        )

    def _on_scroll_changed(self, adjustment):
        """Update sticky header based on scroll position."""
        if not self.list_box.get_first_child():
            self.sticky_header.set_visible(False)
            return

        scroll_y = adjustment.get_value()
        page_size = adjustment.get_page_size()
        visible_top = scroll_y
        substantial_visible_top = visible_top + (page_size * 0.1)  # 10% threshold

        # Find the last scrolled category and description
        last_category = None
        last_description = None
        first_header_visible = False

        child = self.list_box.get_first_child()
        while child:
            if isinstance(child, CommandRow):
                child_top = child.get_allocation().y

                if child_top < substantial_visible_top:
                    if child.command.is_general_description:
                        last_description = child.command
                    else:
                        last_category = child.command.category

                # Check if first category header is visible
                if (
                    child.get_header()
                    and child.get_header().get_visible()
                    and child_top - child.get_header().get_allocation().height
                    <= visible_top + 80
                ):
                    first_header_visible = True

            child = child.get_next_sibling()

        # Determine what to show in sticky header
        show_category = last_category and not first_header_visible
        show_description = last_description and not first_header_visible

        # Update sticky header
        if scroll_y > 0 and (show_category or show_description):
            self.sticky_header.set_visible(True)

            if show_category:
                self.sticky_category_label.set_markup(
                    f"<b>{GLib.markup_escape_text(last_category)}</b>"
                )
                self.sticky_category_box.set_visible(True)
            else:
                self.sticky_category_box.set_visible(False)

            if show_description:
                self.sticky_description_label.set_markup(
                    f"ℹ️ <b>{GLib.markup_escape_text(last_description.name)}</b>: {GLib.markup_escape_text(last_description.description)}"
                )
                self.sticky_description_box.set_visible(True)
            else:
                self.sticky_description_box.set_visible(False)
        else:
            self.sticky_header.set_visible(False)

    def _update_sticky_header(self):
        """Update sticky header content based on current scroll position."""
        if not self.scrolled_window:
            return

        vadjustment = self.scrolled_window.get_vadjustment()
        if vadjustment:
            self._on_scroll_changed(vadjustment)
