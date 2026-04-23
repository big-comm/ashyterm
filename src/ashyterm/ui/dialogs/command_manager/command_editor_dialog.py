"""Command Editor Dialog — full editor for command definitions."""

from typing import Dict, List, Optional

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gio, GObject, Gtk

from ....data.command_manager_models import (
    CommandButton,
    CommandFormField,
    DisplayMode,
    ExecutionMode,
    FieldType,
    generate_id,
)
from ....settings.manager import SettingsManager
from ....utils.accessibility import set_label as a11y_label
from ....utils.tooltip_helper import get_tooltip_helper
from ....utils.translation_utils import _
from ...widgets.action_rows import ManagedExpanderRow
from ..base_dialog import BaseDialog
from .command_editor_data import (
    FIELD_TYPE_ICONS,
    build_form_command_template,
    build_form_fields_list,
    field_type_display_names,
    field_type_to_string,
    form_field_to_dict,
    get_field_extra_config,
    get_field_preview,
    new_field_data,
)
from . import command_field_rows as _rows
from .command_editor_step_simple import (
    build_step_form_info as _build_step_form_info_impl,
    build_step_simple as _build_step_simple_impl,
)
from .command_editor_step_type import (
    build_step_type_choice as _build_step_type_choice_impl,
)


class CommandEditorDialog(Adw.Window):
    """
    Dialog for creating or editing a custom command button.
    Redesigned with simplified UI and modern UX patterns.
    """

    __gsignals__ = {
        "save-requested": (
            GObject.SignalFlags.RUN_FIRST,
            None,
            (GObject.TYPE_PYOBJECT,),
        ),
    }

    def __init__(
        self,
        parent,
        command: Optional[CommandButton] = None,
        settings_manager: Optional[SettingsManager] = None,
    ):
        # Match the Command Manager dialog size
        parent_width = parent.get_width() if parent else 800
        parent_height = parent.get_height() if parent else 600

        super().__init__(
            transient_for=parent,
            modal=True,
            default_width=int(parent_width * 0.7),
            default_height=int(parent_height * 0.75),
        )
        self.add_css_class("ashyterm-dialog")
        self.add_css_class("command-editor-dialog")
        self.command = command
        self.is_new = command is None
        self._selected_icon = "utilities-terminal-symbolic"
        self._settings_manager = settings_manager

        self.set_title(_("New Command") if self.is_new else _("Edit Command"))
        self._build_ui()
        self._apply_color_scheme()

        if not self.is_new:
            self._populate_from_command()

        # Keyboard handler
        key_controller = Gtk.EventControllerKey.new()
        key_controller.connect("key-pressed", self._on_key_pressed)
        self.add_controller(key_controller)

    def _apply_color_scheme(self):
        """Apply terminal color scheme to BashTextView when gtk_theme is 'terminal'.

        Dialog theming is handled globally via the ashyterm-dialog class.
        This method only updates the syntax highlighting in the BashTextView.
        """
        if not self._settings_manager:
            return

        gtk_theme = self._settings_manager.get("gtk_theme", "")
        if gtk_theme != "terminal":
            return

        # Update BashTextView syntax highlighting colors
        scheme = self._settings_manager.get_color_scheme_data()
        palette = scheme.get("palette", [])
        fg_color = scheme.get("foreground", "#ffffff")
        if palette:
            if (
                hasattr(self, "simple_command_textview")
                and self.simple_command_textview
            ):
                self.simple_command_textview.update_colors_from_scheme(
                    palette, fg_color
                )
            if hasattr(self, "command_textview") and self.command_textview:
                self.command_textview.update_colors_from_scheme(palette, fg_color)

    def _on_key_pressed(self, controller, keyval, _keycode, state):
        if keyval == Gdk.KEY_Escape:
            self.close()
            return Gdk.EVENT_STOP
        return Gdk.EVENT_PROPAGATE

    def _build_ui(self):
        # Initialize data structures
        self.form_fields_data: List[Dict] = []
        self._selected_icon = "utilities-terminal-symbolic"
        self._command_type = "simple"  # "simple" or "form"

        # Header with navigation
        header = Adw.HeaderBar()

        # Back button
        self.back_button = Gtk.Button(
            icon_name="go-previous-symbolic",
        )
        a11y_label(self.back_button, _("Back"))
        get_tooltip_helper().add_tooltip(self.back_button, _("Back"))
        self.back_button.connect("clicked", self._on_back_clicked)
        self.back_button.set_visible(False)
        header.pack_start(self.back_button)

        cancel_button = Gtk.Button(label=_("Cancel"))
        cancel_button.connect("clicked", lambda _: self.close())
        header.pack_start(cancel_button)

        # Save button
        self.save_button = Gtk.Button(
            label=_("Save"), css_classes=[BaseDialog.CSS_CLASS_SUGGESTED]
        )
        self.save_button.connect("clicked", self._save_command)
        self.save_button.set_visible(False)
        header.pack_end(self.save_button)

        # Continue button (for form wizard)
        self.continue_button = Gtk.Button(label=_("Continue"))
        self.continue_button.connect("clicked", self._on_continue_clicked)
        self.continue_button.set_visible(False)
        header.pack_end(self.continue_button)

        # Stack for wizard steps
        self.wizard_stack = Gtk.Stack(
            transition_type=Gtk.StackTransitionType.SLIDE_LEFT_RIGHT,
        )

        # Step 0: Choose command type
        self._build_step_type_choice()

        # Step 1: Simple command (basic info + command)
        self._build_step_simple()

        # Step 2a: Form command - Basic info only
        self._build_step_form_info()

        # Step 2b: Form builder (for GUI commands)
        self._build_step_form_builder()

        # Scrolled window
        scrolled = Gtk.ScrolledWindow(
            hscrollbar_policy=Gtk.PolicyType.NEVER,
            vexpand=True,
        )
        scrolled.set_child(self.wizard_stack)

        # Setup actions for adding fields - all field types
        action_group = Gio.SimpleActionGroup()
        all_field_types = [
            "command_text",
            "text",
            "text_area",
            "password",
            "number",
            "slider",
            "switch",
            "dropdown",
            "radio",
            "multi_select",
            "file_path",
            "directory_path",
            "date_time",
            "color",
        ]
        for field_type in all_field_types:
            action = Gio.SimpleAction.new(
                f"add-{field_type.replace('_', '-')}-field", None
            )
            action.connect(
                "activate", lambda *_, ft=field_type: self._add_form_field(ft)
            )
            action_group.add_action(action)
        self.insert_action_group("editor", action_group)

        # Assemble
        toolbar_view = Adw.ToolbarView()
        toolbar_view.add_top_bar(header)
        toolbar_view.set_content(scrolled)
        self.set_content(toolbar_view)

    def _build_step_type_choice(self):
        _build_step_type_choice_impl(self)

    def _select_command_type(self, cmd_type: str):
        """Handle command type selection."""
        self._command_type = cmd_type
        self.back_button.set_visible(True)

        if cmd_type == "simple":
            self.save_button.set_visible(True)
            self.continue_button.set_visible(False)
            self.wizard_stack.set_visible_child_name("simple")
        else:
            # Form command: first show basic info, then form builder
            self.save_button.set_visible(False)
            self.continue_button.set_visible(True)
            self.wizard_stack.set_visible_child_name("form_info")

    def _on_continue_clicked(self, button):
        """Continue to form builder step."""
        # Validate basic info
        name = self.form_name_row.get_text().strip()
        if not name:
            self.form_name_row.add_css_class(BaseDialog.CSS_CLASS_ERROR)
            return
        self.form_name_row.remove_css_class(BaseDialog.CSS_CLASS_ERROR)

        # Go to form builder
        self.continue_button.set_visible(False)
        self.save_button.set_visible(True)
        self.wizard_stack.set_visible_child_name("form_builder")
        self._update_preview()
        self._update_form_preview()

    def _build_step_simple(self):
        _build_step_simple_impl(self)

    def _build_step_form_info(self):
        _build_step_form_info_impl(self)

    def _build_step_form_builder(self):
        """Build Form Builder step: Command parts and preview in split layout."""
        # Main horizontal box to split left/right
        form_builder_step = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL,
            spacing=12,
            margin_top=12,
            margin_bottom=12,
            margin_start=12,
            margin_end=12,
        )

        # LEFT SIDE: Command Parts
        left_box = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=12,
            hexpand=True,
        )

        # Header with title and add button in one line
        parts_header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        parts_header.append(
            Gtk.Label(
                label=_("Add command parts"),
                xalign=0.0,
                hexpand=True,
                css_classes=["title-4"],
            )
        )

        add_field_button = Gtk.MenuButton(
            icon_name="list-add-symbolic",
            css_classes=[BaseDialog.CSS_CLASS_FLAT],
        )
        get_tooltip_helper().add_tooltip(add_field_button, _("Add part"))
        add_menu = Gio.Menu()

        # Basic types section
        basic_section = Gio.Menu()
        basic_section.append(_("📝 Command Text"), "editor.add-command-text-field")
        basic_section.append(_("⌨️ Text Input"), "editor.add-text-field")
        basic_section.append(_("📝 Text Area"), "editor.add-text-area-field")
        basic_section.append(_("🔑 Password"), "editor.add-password-field")
        add_menu.append_section(_("Text"), basic_section)

        # Numeric types section
        numeric_section = Gio.Menu()
        numeric_section.append(_("🔢 Number"), "editor.add-number-field")
        numeric_section.append(_("📊 Slider"), "editor.add-slider-field")
        add_menu.append_section(_("Numbers"), numeric_section)

        # Selection types section
        selection_section = Gio.Menu()
        selection_section.append(_("🔘 Switch"), "editor.add-switch-field")
        selection_section.append(_("📋 Dropdown"), "editor.add-dropdown-field")
        selection_section.append(_("⚪ Radio Buttons"), "editor.add-radio-field")
        selection_section.append(_("☑️ Multi-Select"), "editor.add-multi-select-field")
        add_menu.append_section(_("Selection"), selection_section)

        # File types section
        file_section = Gio.Menu()
        file_section.append(_("📄 File Path"), "editor.add-file-path-field")
        file_section.append(_("📁 Directory Path"), "editor.add-directory-path-field")
        add_menu.append_section(_("Files"), file_section)

        # Special types section
        special_section = Gio.Menu()
        special_section.append(_("📅 Date/Time"), "editor.add-date-time-field")
        special_section.append(_("🎨 Color"), "editor.add-color-field")
        add_menu.append_section(_("Special"), special_section)

        add_field_button.set_menu_model(add_menu)
        parts_header.append(add_field_button)
        left_box.append(parts_header)

        # Scrolled list for fields
        fields_scroll = Gtk.ScrolledWindow(
            hscrollbar_policy=Gtk.PolicyType.NEVER,
            vscrollbar_policy=Gtk.PolicyType.AUTOMATIC,
            vexpand=True,
        )
        self.form_fields_list = Gtk.ListBox(
            selection_mode=Gtk.SelectionMode.NONE,
            css_classes=[BaseDialog.CSS_CLASS_BOXED_LIST],
        )
        fields_scroll.set_child(self.form_fields_list)
        left_box.append(fields_scroll)

        form_builder_step.append(left_box)

        # Separator
        separator = Gtk.Separator(orientation=Gtk.Orientation.VERTICAL)
        form_builder_step.append(separator)

        # RIGHT SIDE: Previews
        right_box = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=12,
            hexpand=True,
        )

        # Command preview
        preview_group = Adw.PreferencesGroup(title=_("Command Preview"))

        self.result_preview = Gtk.Label(
            xalign=0.0,
            selectable=True,
            wrap=True,
            css_classes=["monospace", "heading"],
            margin_start=4,
            margin_top=4,
            margin_bottom=4,
        )
        self.result_preview.set_text(_("(empty)"))
        preview_group.add(self.result_preview)

        right_box.append(preview_group)

        # Form Preview
        form_preview_group = Adw.PreferencesGroup(title=_("Form Preview"))

        # Scrolled list for form preview
        preview_scroll = Gtk.ScrolledWindow(
            hscrollbar_policy=Gtk.PolicyType.NEVER,
            vscrollbar_policy=Gtk.PolicyType.AUTOMATIC,
            vexpand=True,
        )
        self.form_preview_list = Gtk.ListBox(
            selection_mode=Gtk.SelectionMode.NONE,
            css_classes=[BaseDialog.CSS_CLASS_BOXED_LIST],
        )
        preview_scroll.set_child(self.form_preview_list)
        form_preview_group.add(preview_scroll)

        right_box.append(form_preview_group)
        form_builder_step.append(right_box)

        self.wizard_stack.add_named(form_builder_step, "form_builder")

    def _on_back_clicked(self, button):
        """Go back to previous step."""
        current = self.wizard_stack.get_visible_child_name()

        if current == "form_builder":
            # Go back to form info
            self.wizard_stack.set_visible_child_name("form_info")
            self.save_button.set_visible(False)
            self.continue_button.set_visible(True)
        elif current in ("simple", "form_info"):
            # Go back to type choice
            self.wizard_stack.set_visible_child_name("type_choice")
            self.back_button.set_visible(False)
            self.save_button.set_visible(False)
            self.continue_button.set_visible(False)

    def _on_icon_entry_changed(self, entry, mode: str):
        """Update icon preview when icon name changes."""
        icon_name = entry.get_text().strip()
        if icon_name:
            self._selected_icon = icon_name
            if mode == "simple":
                self.simple_icon_preview.set_from_icon_name(icon_name)
            else:
                self.form_icon_preview.set_from_icon_name(icon_name)

    def _on_pick_icon_clicked(self, mode: str):
        """Open system icon picker dialog."""
        # Create a simple icon chooser dialog
        dialog = Adw.AlertDialog(
            heading=_("Choose Icon"),
            body=_("Enter icon name or select from common icons:"),
            default_response="select",
            close_response="cancel",
        )

        # Common terminal/utility icons grid
        common_icons = [
            "utilities-terminal-symbolic",
            "system-run-symbolic",
            "document-save-symbolic",
            "folder-symbolic",
            "edit-copy-symbolic",
            "edit-paste-symbolic",
            "edit-find-symbolic",
            "view-refresh-symbolic",
            "preferences-system-symbolic",
            "network-server-symbolic",
            "drive-harddisk-symbolic",
            "application-x-executable-symbolic",
            "text-x-generic-symbolic",
            "emblem-system-symbolic",
            "media-playback-start-symbolic",
            "process-stop-symbolic",
        ]

        icons_grid = Gtk.FlowBox(
            selection_mode=Gtk.SelectionMode.SINGLE,
            max_children_per_line=8,
            min_children_per_line=4,
            column_spacing=4,
            row_spacing=4,
            margin_top=12,
        )

        selected_icon_name = [self._selected_icon]

        for icon_name in common_icons:
            icon_btn = Gtk.Button(
                css_classes=[BaseDialog.CSS_CLASS_FLAT],
            )
            a11y_label(icon_btn, icon_name)
            get_tooltip_helper().add_tooltip(icon_btn, icon_name)
            icon_btn.set_child(Gtk.Image.new_from_icon_name(icon_name))
            icon_btn.connect(
                "clicked",
                lambda b, n=icon_name: self._select_icon(
                    n, dialog, selected_icon_name, mode
                ),
            )
            icons_grid.insert(icon_btn, -1)

        dialog.set_extra_child(icons_grid)

        dialog.add_response("cancel", _("Cancel"))
        dialog.add_response("select", _("Select"))
        dialog.set_response_appearance("select", Adw.ResponseAppearance.SUGGESTED)

        dialog.connect(
            "response",
            lambda d, r: self._on_icon_dialog_response(d, r, selected_icon_name, mode),
        )
        dialog.present(self)

    def _select_icon(self, icon_name, dialog, selected_ref, mode: str):
        """Handle icon selection from grid."""
        selected_ref[0] = icon_name
        if mode == "simple":
            self.simple_icon_entry.set_text(icon_name)
        else:
            self.form_icon_entry.set_text(icon_name)
        dialog.force_close()

    def _on_icon_dialog_response(self, dialog, response, selected_ref, mode: str):
        """Handle icon picker dialog response."""
        if response == "select":
            if mode == "simple":
                self.simple_icon_entry.set_text(selected_ref[0])
            else:
                self.form_icon_entry.set_text(selected_ref[0])

    def _get_field_preview(self, field_data: Dict) -> str:
        return get_field_preview(field_data)

    def _update_preview(self):
        """Update the resulting command preview from form fields order."""
        parts = []
        for field_data in self.form_fields_data:
            preview = self._get_field_preview(field_data)
            if preview:
                parts.append(preview)

        template = " ".join(parts)
        self.result_preview.set_text(template if template else _("(empty)"))

    def _update_form_preview(self):
        """Update the form preview section with actual widget representations.

        Uses FormWidgetBuilder for consistent widget creation across dialogs.
        """
        from ...widgets.form_widget_builder import create_field_from_dict

        # Clear existing preview
        while True:
            row = self.form_preview_list.get_row_at_index(0)
            if row is None:
                break
            self.form_preview_list.remove(row)

        # Add preview widgets for each form field (skip command_text, it's not shown to user)
        for field_data in self.form_fields_data:
            field_type = field_data.get("type", "text")

            # Skip command_text - it's static and not shown in the form dialog
            if field_type == "command_text":
                continue

            # Use the centralized FormWidgetBuilder for preview (non-interactive)
            row, _ = create_field_from_dict(
                field_data, on_change=None, interactive=False
            )

            tooltip = field_data.get("tooltip", "")
            if tooltip and hasattr(row, "set_tooltip_text"):
                get_tooltip_helper().add_tooltip(row, tooltip)

            self.form_preview_list.append(row)

    def _on_template_text_changed(self, buffer):
        """Handle template text changes (for simple command mode)."""
        pass  # No special handling needed for immediate execution mode

    def _add_form_field(self, field_type: str):
        """Add a new form field (command part)."""
        field_data = new_field_data(
            field_type, position=len(self.form_fields_data) + 1
        )
        self.form_fields_data.append(field_data)

        row = self._create_field_row(field_data, len(self.form_fields_data) - 1)
        self.form_fields_list.append(row)

        self._update_preview()
        self._update_form_preview()

    def _create_field_row(self, field_data: Dict, index: int) -> Adw.ExpanderRow:
        """Create a collapsible UI row for a command part / form field."""
        type_icons = FIELD_TYPE_ICONS
        type_names = field_type_display_names()
        field_type = field_data.get("type", "text")
        field_id = field_data.get("id", f"field_{index + 1}")

        # Create expandable row
        if field_type == "command_text":
            # For command text, show the value in title
            value = field_data.get("default", "")
            title = (
                f"{type_icons.get(field_type, '💬')} {value}"
                if value
                else f"{type_icons.get(field_type, '💬')} {_('(empty)')}"
            )
        else:
            title = f"{type_icons.get(field_type, '📝')} {{{field_id}}}"

        expander = ManagedExpanderRow(
            title=title,
            subtitle=type_names.get(field_type, _("Field")),
            show_reorder=True,
            show_delete=True,
            is_first=(index == 0),
            is_last=(index == len(self.form_fields_data) - 1),
        )

        # Store index for move operations (will be updated when rebuilding)
        expander._field_index = index

        # Connect signals
        expander.connect(
            "move-up-clicked", lambda _: self._move_field_up(expander._field_index)
        )
        expander.connect(
            "move-down-clicked", lambda _: self._move_field_down(expander._field_index)
        )
        expander.connect(
            "delete-clicked",
            lambda _: self._remove_form_field(expander._field_index, expander),
        )

        # Type-specific content
        if field_type == "command_text":
            self._add_cmd_text_rows(expander, field_data, type_icons, field_type)
        else:
            self._add_field_base_rows(expander, field_data, type_icons, field_type)

        # Specialized config rows delegate based on type
        config_methods = {
            "text": self._add_text_rows,
            "number": self._add_number_rows,
            "switch": self._add_switch_rows,
            "dropdown": self._add_dropdown_rows,
            "file_path": self._add_path_rows,
            "directory_path": self._add_path_rows,
            "password": self._add_password_rows,
            "text_area": self._add_textarea_rows,
            "slider": self._add_slider_rows,
            "date_time": self._add_datetime_rows,
            "color": self._add_color_rows,
            "radio": self._add_dropdown_rows,  # Shares logic
            "multi_select": self._add_dropdown_rows,  # Shares logic
        }

        if method := config_methods.get(field_type):
            method(expander, field_data)

        return expander

    def _move_field_up(self, index: int):
        """Move a field up in the list."""
        if index > 0 and index < len(self.form_fields_data):
            # Swap in data
            self.form_fields_data[index], self.form_fields_data[index - 1] = (
                self.form_fields_data[index - 1],
                self.form_fields_data[index],
            )
            # Rebuild UI
            self._rebuild_form_fields_list()
            self._update_preview()
            self._update_form_preview()

    def _move_field_down(self, index: int):
        """Move a field down in the list."""
        if index >= 0 and index < len(self.form_fields_data) - 1:
            # Swap in data
            self.form_fields_data[index], self.form_fields_data[index + 1] = (
                self.form_fields_data[index + 1],
                self.form_fields_data[index],
            )
            # Rebuild UI
            self._rebuild_form_fields_list()
            self._update_preview()
            self._update_form_preview()

    def _rebuild_form_fields_list(self):
        """Rebuild the form fields listbox from data."""
        # Clear
        while True:
            row = self.form_fields_list.get_row_at_index(0)
            if row is None:
                break
            self.form_fields_list.remove(row)

        # Rebuild
        for i, field_data in enumerate(self.form_fields_data):
            row = self._create_field_row(field_data, i)
            self.form_fields_list.append(row)

    def _remove_form_field(self, index: int, row: Gtk.ListBoxRow):
        """Remove a form field."""
        if 0 <= index < len(self.form_fields_data):
            self.form_fields_data.pop(index)
        self.form_fields_list.remove(row)
        # Update indices on remaining rows
        self._rebuild_form_fields_list()
        self._update_preview()
        self._update_form_preview()

    def _populate_from_command(self):
        """Fill form with existing command data."""
        # Determine command type
        is_form_command = self.command.execution_mode == ExecutionMode.SHOW_DIALOG
        self._command_type = "form" if is_form_command else "simple"

        # Skip type choice, go directly to the right step
        self.back_button.set_visible(True)
        self.save_button.set_visible(True)
        self.continue_button.set_visible(False)

        if is_form_command:
            self._populate_form_command_ui()
        else:
            self._populate_simple_command_ui()

    def _populate_form_command_ui(self):
        """Populate UI for form-based commands."""
        self.form_name_row.set_text(self.command.name)
        self.form_description_row.set_text(self.command.description)
        self.form_icon_entry.set_text(self.command.icon_name)
        self._selected_icon = self.command.icon_name
        self.form_icon_preview.set_from_icon_name(self.command.icon_name)

        # Display mode
        mode_idx = {
            DisplayMode.ICON_AND_TEXT: 0,
            DisplayMode.ICON_ONLY: 1,
            DisplayMode.TEXT_ONLY: 2,
        }.get(self.command.display_mode, 0)
        self.form_display_mode_row.set_selected(mode_idx)

        # Populate form fields
        for field in self.command.form_fields:
            field_data = self._convert_field_to_dict(field)
            self.form_fields_data.append(field_data)

        # Rebuild UI
        self._rebuild_form_fields_list()
        self._update_preview()
        self._update_form_preview()

        # Go directly to form builder when editing
        self.wizard_stack.set_visible_child_name("form_builder")

    def _convert_field_to_dict(self, field: CommandFormField) -> Dict:
        return form_field_to_dict(field)

    def _populate_simple_command_ui(self):
        """Populate UI for simple commands."""
        self.simple_name_row.set_text(self.command.name)
        self.simple_description_row.set_text(self.command.description)
        self.simple_command_textview.set_text(self.command.command_template)
        self.simple_icon_entry.set_text(self.command.icon_name)
        self._selected_icon = self.command.icon_name
        self.simple_icon_preview.set_from_icon_name(self.command.icon_name)

        # Display mode
        mode_idx = {
            DisplayMode.ICON_AND_TEXT: 0,
            DisplayMode.ICON_ONLY: 1,
            DisplayMode.TEXT_ONLY: 2,
        }.get(self.command.display_mode, 0)
        self.simple_display_mode_row.set_selected(mode_idx)

        # Execution mode (0 = Insert Only, 1 = Insert and Execute)
        exec_mode_idx = (
            1 if self.command.execution_mode == ExecutionMode.INSERT_AND_EXECUTE else 0
        )
        self.simple_execution_mode_row.set_selected(exec_mode_idx)

        self.wizard_stack.set_visible_child_name("simple")

    def _field_type_to_string(self, field_type: FieldType) -> str:
        return field_type_to_string(field_type)

    def _get_simple_command_data(
        self,
    ) -> tuple[str, str, str, int, str, "ExecutionMode", list]:
        """Extract data from simple command form."""
        name = self.simple_name_row.get_text().strip()
        description = self.simple_description_row.get_text().strip()
        icon_name = (
            self.simple_icon_entry.get_text().strip() or "utilities-terminal-symbolic"
        )
        display_mode_idx = self.simple_display_mode_row.get_selected()
        command_template = self.simple_command_textview.get_text().strip()
        exec_mode_idx = self.simple_execution_mode_row.get_selected()
        exec_mode = (
            ExecutionMode.INSERT_ONLY
            if exec_mode_idx == 0
            else ExecutionMode.INSERT_AND_EXECUTE
        )
        return (
            name,
            description,
            icon_name,
            display_mode_idx,
            command_template,
            exec_mode,
            [],
        )

    def _build_form_command_template(self) -> str:
        return build_form_command_template(self.form_fields_data)

    def _get_field_extra_config(self, field_data: dict) -> dict:
        return get_field_extra_config(field_data)

    def _build_form_fields_list(self) -> list:
        return build_form_fields_list(self.form_fields_data)

    def _get_form_command_data(
        self,
    ) -> tuple[str, str, str, int, str, "ExecutionMode", list]:
        """Extract data from form command form."""
        name = self.form_name_row.get_text().strip()
        description = self.form_description_row.get_text().strip()
        icon_name = (
            self.form_icon_entry.get_text().strip() or "utilities-terminal-symbolic"
        )
        display_mode_idx = self.form_display_mode_row.get_selected()
        exec_mode = ExecutionMode.SHOW_DIALOG
        command_template = self._build_form_command_template()
        form_fields = self._build_form_fields_list()
        return (
            name,
            description,
            icon_name,
            display_mode_idx,
            command_template,
            exec_mode,
            form_fields,
        )

    def _save_command(self, *args):
        """Save the command."""
        if self._command_type == "simple":
            (
                name,
                description,
                icon_name,
                display_mode_idx,
                command_template,
                exec_mode,
                form_fields,
            ) = self._get_simple_command_data()
        else:
            (
                name,
                description,
                icon_name,
                display_mode_idx,
                command_template,
                exec_mode,
                form_fields,
            ) = self._get_form_command_data()

        if not name:
            if self._command_type == "simple":
                self.simple_name_row.add_css_class(BaseDialog.CSS_CLASS_ERROR)
            else:
                self.form_name_row.add_css_class(BaseDialog.CSS_CLASS_ERROR)
            return

        if not command_template:
            return

        display_modes = [
            DisplayMode.ICON_AND_TEXT,
            DisplayMode.ICON_ONLY,
            DisplayMode.TEXT_ONLY,
        ]
        display_mode = display_modes[display_mode_idx]

        is_builtin = self.command.is_builtin if self.command else False

        new_command = CommandButton(
            id=self.command.id if self.command else generate_id(),
            name=name,
            description=description,
            command_template=command_template,
            icon_name=icon_name,
            display_mode=display_mode,
            execution_mode=exec_mode,
            cursor_position=0,
            category=_("Custom"),
            is_builtin=is_builtin,
            form_fields=form_fields,
        )

        self.emit("save-requested", new_command)
        self.close()

    # ── Field-row delegators ─────────────────────────────────
    # Each builder lives in ``command_field_rows``. Thin shims keep
    # the method names that ``_create_field_row`` dispatches to.

    def _add_cmd_text_rows(self, expander, field_data, type_icons, field_type):
        _rows.add_cmd_text_rows(self, expander, field_data, type_icons, field_type)

    def _add_field_base_rows(self, expander, field_data, type_icons, field_type):
        _rows.add_field_base_rows(self, expander, field_data, type_icons, field_type)

    def _add_text_rows(self, expander, field_data):
        _rows.add_text_rows(self, expander, field_data)

    def _add_number_rows(self, expander, field_data):
        _rows.add_number_rows(self, expander, field_data)

    def _add_switch_rows(self, expander, field_data):
        _rows.add_switch_rows(self, expander, field_data)

    def _add_dropdown_rows(self, expander, field_data):
        _rows.add_dropdown_rows(self, expander, field_data)

    def _sync_dropdown_options(self, listbox, field_data):
        _rows.sync_dropdown_options(self, listbox, field_data)

    def _create_option_row(self, value, label, sync_cb, listbox):
        return _rows.create_option_row(value, label, sync_cb, listbox)

    def _add_path_rows(self, expander, field_data):
        _rows.add_path_rows(self, expander, field_data)

    def _add_password_rows(self, expander, field_data):
        _rows.add_password_rows(self, expander, field_data)

    def _add_textarea_rows(self, expander, field_data):
        _rows.add_textarea_rows(self, expander, field_data)

    def _add_slider_rows(self, expander, field_data):
        _rows.add_slider_rows(self, expander, field_data)

    def _add_datetime_rows(self, expander, field_data):
        _rows.add_datetime_rows(self, expander, field_data)

    def _add_color_rows(self, expander, field_data):
        _rows.add_color_rows(self, expander, field_data)
