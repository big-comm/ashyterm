# ashyterm/ui/dialogs/command_guide_dialog.py

from typing import List, Optional

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, GLib, GObject, Gtk, Pango

from ...data.commands import CommandItem, get_command_manager
from ...utils.tooltip_helper import get_tooltip_helper
from ...utils.translation_utils import _


class CommandEditDialog(Adw.Window):
    """Dialog for adding or editing a custom command with improved UI/UX."""

    __gsignals__ = {
        "save-requested": (
            GObject.SignalFlags.RUN_FIRST,
            None,
            (GObject.TYPE_PYOBJECT, GObject.TYPE_PYOBJECT),
        ),
    }

    def __init__(
        self,
        parent,
        all_categories: List[str],
        original_command: Optional[CommandItem] = None,
    ):
        super().__init__(
            transient_for=parent, modal=True, default_width=600, default_height=500
        )
        self.original_command = original_command
        self.all_categories = all_categories
        self.is_new = original_command is None
        self.set_title(
            _("Add Custom Command") if self.is_new else _("Edit Custom Command")
        )

        self._build_ui()

        if not self.is_new:
            self.name_view.get_buffer().set_text(original_command.name)
            self.category_entry.set_text(original_command.category)
            self.description_view.get_buffer().set_text(original_command.description)

        self.connect("map", self._on_map)

        # Adds keyboard event handler to capture Esc key.
        key_controller = Gtk.EventControllerKey.new()
        key_controller.connect("key-pressed", self._on_key_pressed)
        self.add_controller(key_controller)

    def _on_map(self, widget):
        """Set focus to the name entry when the dialog is shown."""
        self.name_view.grab_focus()

    # Method for handling key press.
    def _on_key_pressed(self, controller, keyval, keycode, state):
        """Handles key press events to close the dialog on Escape."""
        if keyval == Gdk.KEY_Escape:
            self.close()
            return Gdk.EVENT_STOP
        return Gdk.EVENT_PROPAGATE

    def _build_ui(self):
        header = Adw.HeaderBar()
        cancel_button = Gtk.Button(label=_("Cancel"), valign=Gtk.Align.CENTER)
        cancel_button.connect("clicked", lambda _: self.close())
        header.pack_start(cancel_button)

        save_button = Gtk.Button(label=_("Save"), css_classes=["suggested-action"], valign=Gtk.Align.CENTER)
        save_button.connect("clicked", self._on_save)
        header.pack_end(save_button)
        self.set_default_widget(save_button)

        # Use a Gtk.Box for a flexible form layout instead of Adw.PreferencesGroup
        form_box = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=12,
            margin_top=12,
            margin_bottom=12,
            margin_start=12,
            margin_end=12,
        )

        # Command Name Field
        command_label = Gtk.Label(
            label=_("Command"), xalign=0.0, css_classes=["title-4"]
        )
        form_box.append(command_label)
        self.name_view = Gtk.TextView(
            wrap_mode=Gtk.WrapMode.WORD_CHAR,
            vexpand=False,
            pixels_above_lines=4,
            pixels_below_lines=4,
            left_margin=6,
            right_margin=6,
        )
        self.name_view.add_css_class("monospace")
        scrolled_name = Gtk.ScrolledWindow(
            min_content_height=60, hscrollbar_policy=Gtk.PolicyType.NEVER
        )
        scrolled_name.set_child(self.name_view)
        scrolled_name.add_css_class("card")
        form_box.append(scrolled_name)

        # Category Field
        category_label = Gtk.Label(
            label=_("Category"), xalign=0.0, css_classes=["title-4"]
        )
        form_box.append(category_label)
        self.category_entry = Gtk.Entry(
            hexpand=True,
            placeholder_text=_("Select an existing category or type a new one"),
        )
        completion_model = Gtk.ListStore.new([str])
        for category in sorted(self.all_categories):
            completion_model.append([category])
        completion = Gtk.EntryCompletion()
        completion.set_model(completion_model)
        completion.set_text_column(0)
        self.category_entry.set_completion(completion)
        form_box.append(self.category_entry)

        # Description Field
        description_label = Gtk.Label(
            label=_("Description"), xalign=0.0, css_classes=["title-4"]
        )
        form_box.append(description_label)
        self.description_view = Gtk.TextView(
            wrap_mode=Gtk.WrapMode.WORD_CHAR,
            vexpand=True,
            pixels_above_lines=4,
            pixels_below_lines=4,
            left_margin=6,
            right_margin=6,
        )
        scrolled_desc = Gtk.ScrolledWindow(
            min_content_height=100, hscrollbar_policy=Gtk.PolicyType.NEVER
        )
        scrolled_desc.set_child(self.description_view)
        scrolled_desc.add_css_class("card")
        form_box.append(scrolled_desc)

        scrolled_page = Gtk.ScrolledWindow(
            hscrollbar_policy=Gtk.PolicyType.NEVER, vexpand=True
        )
        scrolled_page.set_child(form_box)

        toolbar_view = Adw.ToolbarView()
        toolbar_view.add_top_bar(header)
        toolbar_view.set_content(scrolled_page)
        self.set_content(toolbar_view)

    def _on_save(self, _button):
        # Clear previous errors
        self.name_view.remove_css_class("error")
        self.description_view.remove_css_class("error")

        name_buffer = self.name_view.get_buffer()
        name = name_buffer.get_text(
            name_buffer.get_start_iter(), name_buffer.get_end_iter(), True
        ).strip()
        category = self.category_entry.get_text().strip()
        desc_buffer = self.description_view.get_buffer()
        description = desc_buffer.get_text(
            desc_buffer.get_start_iter(), desc_buffer.get_end_iter(), True
        ).strip()

        # Validation
        validation_passed = True
        if not name:
            self.name_view.add_css_class("error")
            validation_passed = False
        if not description:
            self.description_view.add_css_class("error")
            validation_passed = False

        if not validation_passed:
            if hasattr(self.get_transient_for(), "toast_overlay"):
                toast = Adw.Toast(title=_("Please fill in all required fields."))
                self.get_transient_for().toast_overlay.add_toast(toast)
            return

        if not category:
            category = _("Custom")

        new_command = CommandItem(name, category, description, is_custom=True)
        self.emit("save-requested", self.original_command, new_command)
        self.close()


class CommandRow(Gtk.ListBoxRow):
    """Custom widget for displaying a command in the ListBox with improved visual design."""

    def __init__(self, command: CommandItem, on_delete_callback=None):
        super().__init__()
        self.command = command
        self.on_delete_callback = on_delete_callback

        card = Gtk.Box(css_classes=["command-guide-card"], valign=Gtk.Align.CENTER)
        self.set_child(card)

        if command.is_general_description:
            self.set_activatable(False)
            self.set_selectable(False)
            self.set_focusable(False)

            # Info box with icon and description
            info_box = Gtk.Box(
                orientation=Gtk.Orientation.HORIZONTAL,
                spacing=10,
                hexpand=True,
            )
            info_box.add_css_class("general-description")

            # Info icon
            info_icon = Gtk.Label(label="ℹ️")
            info_icon.add_css_class("info-icon")
            info_box.append(info_icon)

            # Description text
            desc_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, hexpand=True)
            title_label = Gtk.Label(xalign=0.0, use_markup=True)
            title_label.set_markup(f"<b>{GLib.markup_escape_text(command.name)}</b>")
            title_label.add_css_class("info-title")
            desc_box.append(title_label)

            desc_label = Gtk.Label(
                xalign=0.0, wrap=True, wrap_mode=Pango.WrapMode.WORD_CHAR
            )
            desc_label.set_text(command.description)
            desc_label.add_css_class("info-description")
            desc_box.append(desc_label)

            info_box.append(desc_box)
            card.append(info_box)
        else:
            self.set_activatable(True)

            # Main vertical layout for better stacking on narrow windows
            main_box = Gtk.Box(
                orientation=Gtk.Orientation.VERTICAL,
                spacing=6,
                hexpand=True,
            )
            card.append(main_box)

            # Top row: command name with optional delete button
            top_row = Gtk.Box(
                orientation=Gtk.Orientation.HORIZONTAL,
                spacing=8,
            )
            main_box.append(top_row)

            # Command name in a code-style box
            name_frame = Gtk.Box(hexpand=True)
            name_frame.add_css_class("command-name-frame")

            name_label = Gtk.Label(xalign=0.0, use_markup=True, selectable=False)
            name_label.set_markup(f"<tt>{GLib.markup_escape_text(command.name)}</tt>")
            name_label.set_wrap(True)
            name_label.set_wrap_mode(Pango.WrapMode.CHAR)
            name_label.set_hexpand(True)
            name_label.add_css_class("command-name")
            name_frame.append(name_label)
            top_row.append(name_frame)

            # Add delete button for custom commands
            if command.is_custom and self.on_delete_callback:
                delete_button = Gtk.Button(
                    icon_name="user-trash-symbolic",
                    css_classes=["flat", "circular", "destructive-action"],
                    tooltip_text=_("Delete this custom command"),
                    valign=Gtk.Align.CENTER,
                )
                delete_button.connect("clicked", self._on_delete_clicked)
                top_row.append(delete_button)

            # Bottom row: description
            desc_label = Gtk.Label(
                xalign=0.0,
                wrap=True,
                wrap_mode=Pango.WrapMode.WORD_CHAR,
                hexpand=True,
            )
            desc_label.set_text(command.description)
            desc_label.add_css_class("command-description")
            main_box.append(desc_label)

    def _on_delete_clicked(self, button):
        if self.on_delete_callback:
            self.on_delete_callback(self.command)


class CommandGuideDialog(Adw.Window):
    """Dialog to display a searchable list of useful commands."""

    __gsignals__ = {
        "command-selected": (GObject.SignalFlags.RUN_FIRST, None, (str,)),
    }

    def __init__(self, parent_window):
        super().__init__(transient_for=parent_window, modal=False)
        self.parent_window = parent_window  # Store reference to main window
        self.command_manager = get_command_manager()
        self.all_commands: List[CommandItem] = []

        # Store references to event controllers for cleanup
        self._event_controllers = []
        self._allow_destroy = False  # Control destruction
        self._presenting = False  # Flag to prevent close during present

        parent_width = parent_window.get_width()
        parent_height = parent_window.get_height()
        self.set_default_size(int(parent_width * 0.8), int(parent_height * 0.85))
        self.set_title(_("Command Guide"))

        self.connect("notify::is-active", self._on_active_changed)
        self.connect("destroy", self._on_destroy)
        self.connect("close-request", self._on_close_request)
        self.connect("show", self._on_show)
        self.connect("hide", self._on_hide)

        # Connect to parent window destroy signal
        if parent_window:
            parent_window.connect("destroy", self._on_parent_destroyed)

        self._build_ui()
        self._populate_list()
        self._set_initial_selection()

        # Initialize sticky header after UI is built
        GLib.idle_add(self._update_sticky_header)

        key_controller = Gtk.EventControllerKey.new()
        key_controller.connect("key-pressed", self._on_window_key_pressed)
        self.add_controller(key_controller)
        self._event_controllers.append(key_controller)

    def present(self):
        """Override present to set flag preventing close during presentation."""
        self._presenting = True
        super().present()
        GLib.idle_add(lambda: setattr(self, '_presenting', False))

    def _build_ui(self):
        css_provider = Gtk.CssProvider()
        css_provider.load_from_data(
            b"""
            .command-guide-card {
                padding: 12px 14px;
                margin: 4px 14px;
                transition: all 0.2s ease;
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
                margin-top: 20px;
            }
            .general-description {
                background: alpha(@theme_selected_bg_color, 0.08);
                border-radius: 8px;
                padding: 12px 14px;
                margin: 2px 0;
                border-left: 4px solid @accent_color;
            }
            .info-icon {
                font-size: 1.3em;
            }
            .info-title {
                font-size: 1.05em;
                color: @theme_fg_color;
            }
            .info-description {
                font-size: 0.95em;
                color: alpha(@theme_fg_color, 0.85);
                margin-top: 4px;
            }
            .command-name-frame {
                background-color: alpha(@theme_fg_color, 0.06);
                border-radius: 6px;
                padding: 8px 12px;
                border: 1px solid alpha(@theme_fg_color, 0.12);
            }
            .command-name {
                font-weight: 600;
                color: @theme_fg_color;
                font-family: 'Monospace';
                font-size: 0.95em;
            }
            .command-description {
                color: alpha(@theme_fg_color, 0.9);
                font-size: 0.95em;
                line-height: 1.5;
                padding-left: 4px;
            }
            .command-guide-background {
                background-color: var(--headerbar-bg-color);
            }
            .command-guide-boxed-list {
                background-color: var(--headerbar-bg-color);
                border-radius: 4px;
                padding: 3px;
                border: 1px solid alpha(@borders, 0.3);
            }
            .command-guide-boxed-list > row:hover .command-guide-card {
                background-color: alpha(@theme_selected_bg_color, 0.15);
                border-radius: 8px;
            }
            .command-guide-boxed-list > row:selected .command-guide-card,
            .command-guide-card.selected {
                background-color: alpha(@theme_selected_bg_color, 0.25);
                border-radius: 8px;
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
            placeholder_text=_("Search commands..."), valign=Gtk.Align.CENTER
        )
        self.search_entry.connect("search-changed", lambda *_: self._filter_list())
        self.search_entry.connect("activate", self._on_search_activate)

        search_key_controller = Gtk.EventControllerKey.new()
        search_key_controller.connect("key-pressed", self._on_search_key_pressed)
        self.search_entry.add_controller(search_key_controller)
        self._event_controllers.append(search_key_controller)

        header.pack_start(self.search_entry)

        self.custom_only_switch = Gtk.Switch(valign=Gtk.Align.CENTER)
        self.custom_only_switch.connect(
            "notify::active", lambda *_: self._filter_list()
        )

        switch_box = Gtk.Box(spacing=6)
        switch_box.append(
            Gtk.Label(label=_("Show Only Custom Commands"), valign=Gtk.Align.CENTER)
        )
        switch_box.append(self.custom_only_switch)
        header.set_title_widget(switch_box)

        add_button = Gtk.Button(label=_("Add New Command"), valign=Gtk.Align.CENTER)
        add_button.connect("clicked", self._on_add_clicked)
        header.pack_end(add_button)

        scrolled_window = Gtk.ScrolledWindow(
            vexpand=True, hscrollbar_policy=Gtk.PolicyType.NEVER
        )
        scrolled_window.add_css_class("command-guide-background")
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
            selection_mode=Gtk.SelectionMode.SINGLE, css_classes=["command-guide-boxed-list"]
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

    def _populate_list(self):
        self.all_commands = self.command_manager.get_all_commands()

        # MODIFIED: Define the sorting key function
        def sort_key(cmd: CommandItem):
            if cmd.is_custom:
                # Sort custom commands by category name, then by name
                return (0, cmd.category, cmd.name)
            else:
                # Sort native commands by their original index
                return (1, cmd.native_sort_index)

        self.all_commands.sort(key=sort_key)
        self._filter_list()

    def _filter_list(self):
        while child := self.list_box.get_first_child():
            self.list_box.remove(child)

        search_term = self.search_entry.get_text().lower()
        custom_only = self.custom_only_switch.get_active()

        for command in self.all_commands:
            if custom_only and not command.is_custom:
                continue
            if search_term and command.is_general_description:
                continue

            show = not search_term or (
                search_term in command.name.lower()
                or search_term in command.description.lower()
                or search_term in command.category.lower()
            )

            if show:
                row = CommandRow(
                    command, on_delete_callback=self._on_delete_row_clicked
                )
                self.list_box.append(row)

        self.list_box.invalidate_headers()
        GLib.idle_add(self._update_sticky_header)

    def _update_header(self, row: CommandRow, before: Optional[CommandRow]):
        if row.get_header() is None:
            command_item = row.command

            header_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
            header_box.add_css_class("category-header")

            icon_label = Gtk.Label(label="📁", css_classes=["category-icon"])
            get_tooltip_helper().add_tooltip(icon_label, _("Category"))
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
        if keyval == Gdk.KEY_Escape:
            self.close()
            return Gdk.EVENT_STOP
        elif keyval == Gdk.KEY_Down:
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

        if state & (Gdk.ModifierType.CONTROL_MASK | Gdk.ModifierType.ALT_MASK):
            return Gdk.EVENT_PROPAGATE

        unicode_val = Gdk.keyval_to_unicode(keyval)
        if unicode_val and chr(unicode_val).isprintable():
            self.search_entry.grab_focus()
            current_text = self.search_entry.get_text()
            self.search_entry.set_text(current_text + chr(unicode_val))
            self.search_entry.set_position(-1)
            return Gdk.EVENT_STOP

        return Gdk.EVENT_PROPAGATE

    def _on_destroy(self, widget):
        self._cleanup_resources()

    def close(self):
        """Override close to hide instead of destroy the dialog."""
        self.hide()

    def destroy(self):
        """Override destroy to prevent accidental destruction."""
        # Only allow destruction if explicitly requested
        if not hasattr(self, '_allow_destroy') or not self._allow_destroy:
            self.hide()
            return
        super().destroy()

    def _on_parent_destroyed(self, parent_window):
        """Handle parent window destruction by allowing dialog destruction."""
        self._allow_destroy = True
        self.destroy()

    def _on_show(self, widget):
        """Handle dialog show event."""

    def _on_hide(self, widget):
        """Handle dialog hide event."""
        self.search_entry.set_text("")

    def _on_close_request(self, widget):
        self.search_entry.set_text("")
        self.hide()
        return Gdk.EVENT_STOP

    def _cleanup_resources(self):
        try:
            while child := self.list_box.get_first_child():
                self.list_box.remove(child)
            self.all_commands.clear()
            if hasattr(self, "scrolled_window") and self.scrolled_window:
                vadjustment = self.scrolled_window.get_vadjustment()
                if vadjustment and hasattr(self, "_scroll_handler_id"):
                    if GObject.signal_handler_is_connected(
                        vadjustment, self._scroll_handler_id
                    ):
                        vadjustment.disconnect(self._scroll_handler_id)
                    del self._scroll_handler_id
        except Exception as e:
            print(f"Warning: Error during CommandGuideDialog cleanup: {e}")

    def _on_active_changed(self, widget, pspec):
        if not self._presenting and not self.is_active() and self.get_visible():
            GLib.timeout_add(200, self._delayed_close)

    def _delayed_close(self):
        if not self.is_active() and self.get_visible():
            self.close()
        return False

    def _on_add_clicked(self, _button):
        all_categories = list(self.command_manager.get_all_categories())
        dialog = CommandEditDialog(self.parent_window, all_categories=all_categories)
        dialog.connect("save-requested", self._on_save_new)
        dialog.present()

    def _on_delete_row_clicked(self, command_item: CommandItem):
        dialog = Adw.MessageDialog(
            transient_for=self.parent_window,
            heading=_("Delete Custom Command?"),
            body=_(
                "Are you sure you want to permanently delete the command:\n\n<b>{name}</b>"
            ).format(name=GLib.markup_escape_text(command_item.name)),
            body_use_markup=True,
            default_response="cancel",
            close_response="cancel",
        )
        dialog.add_response("cancel", _("Cancel"))
        dialog.add_response("delete", _("Delete"))
        dialog.set_response_appearance("delete", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.connect("response", self._on_delete_confirm, command_item)
        dialog.present()

    def _on_delete_confirm(self, dialog, response, command_item):
        if response == "delete":
            self.command_manager.remove_custom_command(command_item)
            self._populate_list()

    def _on_save_new(self, _dialog, _original, new_command):
        self.command_manager.add_custom_command(new_command)
        self._populate_list()

    def _scroll_to_row(self, row):
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
        self.search_entry.grab_focus()

    def _update_selection_styling(self, selected_row):
        child = self.list_box.get_first_child()
        while child:
            if hasattr(child, "get_child"):
                card = child.get_child()
                if card and hasattr(card, "remove_css_class"):
                    card.remove_css_class("selected")
            child = child.get_next_sibling()
        if selected_row and hasattr(selected_row, "get_child"):
            card = selected_row.get_child()
            if card and hasattr(card, "add_css_class"):
                card.add_css_class("selected")

    def _on_scroll_changed(self, adjustment):
        if not self.list_box.get_first_child():
            self.sticky_header.set_visible(False)
            return
        scroll_y = adjustment.get_value()
        page_size = adjustment.get_page_size()
        visible_top = scroll_y
        substantial_visible_top = visible_top + (page_size * 0.1)
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
                if (
                    child.get_header()
                    and child.get_header().get_visible()
                    and child_top - child.get_header().get_allocation().height
                    <= visible_top + 80
                ):
                    first_header_visible = True
            child = child.get_next_sibling()
        show_category = last_category and not first_header_visible
        show_description = last_description and not first_header_visible
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
        if not self.scrolled_window:
            return
        vadjustment = self.scrolled_window.get_vadjustment()
        if vadjustment:
            self._on_scroll_changed(vadjustment)
