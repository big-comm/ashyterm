"""Command Editor Dialog — full editor for command definitions."""

from typing import Any, Dict, List, Optional

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
from ...widgets.bash_text_view import BashTextView
from ..base_dialog import BaseDialog


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
        """Build Step 0: Choose command type."""
        step0 = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=24,
            margin_top=32,
            margin_bottom=32,
            margin_start=24,
            margin_end=24,
            valign=Gtk.Align.CENTER,
        )

        title = Gtk.Label(
            label=_("What type of command do you want to create?"),
            css_classes=["title-2"],
        )
        step0.append(title)

        buttons_box = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=12,
            margin_top=16,
        )

        # Simple command option
        simple_btn = Gtk.Button(css_classes=["card", "flat"])
        a11y_label(simple_btn, _("Simple Command"))
        simple_box = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=8,
            margin_top=16,
            margin_bottom=16,
            margin_start=16,
            margin_end=16,
        )
        simple_icon = Gtk.Image.new_from_icon_name("utilities-terminal-symbolic")
        simple_icon.set_pixel_size(48)
        simple_box.append(simple_icon)
        simple_title = Gtk.Label(label=_("Simple Command"), css_classes=["title-3"])
        simple_box.append(simple_title)
        simple_desc = Gtk.Label(
            label=_("A button that runs a command directly"),
            css_classes=[BaseDialog.CSS_CLASS_DIM_LABEL],
            wrap=True,
        )
        simple_box.append(simple_desc)
        simple_btn.set_child(simple_box)
        simple_btn.connect("clicked", lambda _: self._select_command_type("simple"))
        buttons_box.append(simple_btn)

        # Form command option
        form_btn = Gtk.Button(css_classes=["card", "flat"])
        a11y_label(form_btn, _("Command with Form"))
        form_box = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=8,
            margin_top=16,
            margin_bottom=16,
            margin_start=16,
            margin_end=16,
        )
        form_icon = Gtk.Image.new_from_icon_name("view-paged-symbolic")
        form_icon.set_pixel_size(48)
        form_box.append(form_icon)
        form_title = Gtk.Label(label=_("Command with Form"), css_classes=["title-3"])
        form_box.append(form_title)
        form_desc = Gtk.Label(
            label=_("A button that shows a form to configure the command"),
            css_classes=[BaseDialog.CSS_CLASS_DIM_LABEL],
            wrap=True,
        )
        form_box.append(form_desc)
        form_btn.set_child(form_box)
        form_btn.connect("clicked", lambda _: self._select_command_type("form"))
        buttons_box.append(form_btn)

        step0.append(buttons_box)
        self.wizard_stack.add_named(step0, "type_choice")

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
        """Build Simple Command step: Basic info + Command only."""
        simple_step = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=16,
            margin_top=16,
            margin_bottom=16,
            margin_start=16,
            margin_end=16,
        )

        # Basic Information
        basic_group = Adw.PreferencesGroup(title=_("Basic Information"))

        self.simple_name_row = Adw.EntryRow(title=_("Name"))
        basic_group.add(self.simple_name_row)

        self.simple_description_row = Adw.EntryRow(title=_("Description"))
        basic_group.add(self.simple_description_row)

        # Icon row
        icon_row = Adw.ActionRow(title=_("Icon"))
        self.simple_icon_preview = Gtk.Image.new_from_icon_name(
            "utilities-terminal-symbolic"
        )
        self.simple_icon_preview.set_pixel_size(24)
        icon_row.add_prefix(self.simple_icon_preview)

        icon_picker_btn = Gtk.Button(
            icon_name="view-grid-symbolic",
            css_classes=[BaseDialog.CSS_CLASS_FLAT],
            valign=Gtk.Align.CENTER,
        )
        get_tooltip_helper().add_tooltip(icon_picker_btn, _("Choose icon"))
        icon_picker_btn.connect(
            "clicked", lambda _: self._on_pick_icon_clicked("simple")
        )
        icon_row.add_suffix(icon_picker_btn)

        self.simple_icon_entry = Gtk.Entry(
            placeholder_text="utilities-terminal-symbolic",
            width_chars=20,
            valign=Gtk.Align.CENTER,
        )
        a11y_label(self.simple_icon_entry, _("Icon name"))
        self.simple_icon_entry.set_text("utilities-terminal-symbolic")
        self.simple_icon_entry.connect(
            "changed", lambda e: self._on_icon_entry_changed(e, "simple")
        )
        icon_row.add_suffix(self.simple_icon_entry)
        basic_group.add(icon_row)

        # Display mode
        self.simple_display_mode_row = Adw.ComboRow(title=_("Display Mode"))
        display_modes = Gtk.StringList()
        for mode in [_("Icon and Text"), _("Icon Only"), _("Text Only")]:
            display_modes.append(mode)
        self.simple_display_mode_row.set_model(display_modes)
        basic_group.add(self.simple_display_mode_row)

        # Execution mode (Insert Only or Insert and Execute)
        self.simple_execution_mode_row = Adw.ComboRow(title=_("Execution Mode"))
        execution_modes = Gtk.StringList()
        for mode in [_("Insert Only"), _("Insert and Execute")]:
            execution_modes.append(mode)
        self.simple_execution_mode_row.set_model(execution_modes)
        self.simple_execution_mode_row.set_selected(0)  # Default: Insert Only
        basic_group.add(self.simple_execution_mode_row)

        simple_step.append(basic_group)

        # Command
        command_group = Adw.PreferencesGroup(title=_("Command"))

        help_label = Gtk.Label(
            label=_("Enter the bash command to execute:"),
            xalign=0.0,
            css_classes=[BaseDialog.CSS_CLASS_DIM_LABEL, "caption"],
            margin_start=4,
        )
        command_group.add(help_label)

        command_frame = Gtk.Frame(css_classes=["view"])
        command_scroll = Gtk.ScrolledWindow(
            hscrollbar_policy=Gtk.PolicyType.NEVER,
            vscrollbar_policy=Gtk.PolicyType.AUTOMATIC,
            min_content_height=80,
            max_content_height=150,
        )
        self.simple_command_textview = BashTextView()
        command_scroll.set_child(self.simple_command_textview)
        command_frame.set_child(command_scroll)
        command_group.add(command_frame)

        simple_step.append(command_group)

        self.wizard_stack.add_named(simple_step, "simple")

    def _build_step_form_info(self):
        """Build Form Info step: Basic information only."""
        form_info_step = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=16,
            margin_top=16,
            margin_bottom=16,
            margin_start=16,
            margin_end=16,
        )

        # Basic Information for form command
        basic_group = Adw.PreferencesGroup(title=_("Basic Information"))

        self.form_name_row = Adw.EntryRow(title=_("Name"))
        basic_group.add(self.form_name_row)

        self.form_description_row = Adw.EntryRow(title=_("Description"))
        basic_group.add(self.form_description_row)

        # Icon row
        icon_row = Adw.ActionRow(title=_("Icon"))
        self.form_icon_preview = Gtk.Image.new_from_icon_name(
            "utilities-terminal-symbolic"
        )
        self.form_icon_preview.set_pixel_size(24)
        icon_row.add_prefix(self.form_icon_preview)

        icon_picker_btn = Gtk.Button(
            icon_name="view-grid-symbolic",
            css_classes=[BaseDialog.CSS_CLASS_FLAT],
            valign=Gtk.Align.CENTER,
        )
        get_tooltip_helper().add_tooltip(icon_picker_btn, _("Choose icon"))
        icon_picker_btn.connect("clicked", lambda _: self._on_pick_icon_clicked("form"))
        icon_row.add_suffix(icon_picker_btn)

        self.form_icon_entry = Gtk.Entry(
            placeholder_text="utilities-terminal-symbolic",
            width_chars=20,
            valign=Gtk.Align.CENTER,
        )
        a11y_label(self.form_icon_entry, _("Icon name"))
        self.form_icon_entry.set_text("utilities-terminal-symbolic")
        self.form_icon_entry.connect(
            "changed", lambda e: self._on_icon_entry_changed(e, "form")
        )
        icon_row.add_suffix(self.form_icon_entry)
        basic_group.add(icon_row)

        # Display mode
        self.form_display_mode_row = Adw.ComboRow(title=_("Display Mode"))
        display_modes = Gtk.StringList()
        for mode in [_("Icon and Text"), _("Icon Only"), _("Text Only")]:
            display_modes.append(mode)
        self.form_display_mode_row.set_model(display_modes)
        basic_group.add(self.form_display_mode_row)

        form_info_step.append(basic_group)

        self.wizard_stack.add_named(form_info_step, "form_info")

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
        dialog = Adw.MessageDialog(
            transient_for=self,
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
        dialog.present()

    def _select_icon(self, icon_name, dialog, selected_ref, mode: str):
        """Handle icon selection from grid."""
        selected_ref[0] = icon_name
        if mode == "simple":
            self.simple_icon_entry.set_text(icon_name)
        else:
            self.form_icon_entry.set_text(icon_name)
        dialog.close()

    def _on_icon_dialog_response(self, dialog, response, selected_ref, mode: str):
        """Handle icon picker dialog response."""
        if response == "select":
            if mode == "simple":
                self.simple_icon_entry.set_text(selected_ref[0])
            else:
                self.form_icon_entry.set_text(selected_ref[0])

    def _get_field_preview(self, field_data: Dict) -> str:
        """Get a string representation of a field for the command preview."""
        field_type = field_data.get("type", "text")
        field_id = field_data.get("id", "")

        if field_type == "command_text":
            return field_data.get("default", "")

        if field_type == "switch":
            on_val = field_data.get("command_flag", "")
            off_val = field_data.get("off_value", "")
            if on_val:
                return f"[{on_val}]"
            if off_val:
                return f"[{off_val}]"
            return f"{{{field_id}}}"

        # Regular field types
        default = field_data.get("default", "")
        if default:
            return str(default)

        # Fallback to label or ID
        placeholder = field_data.get("placeholder", "")
        if placeholder:
            return f"<{placeholder}>"

        label = field_data.get("label", "").strip()
        if label:
            return f"<{label}>"

        return f"{{{field_id}}}"

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
        # Generate a unique ID for the field
        field_num = len(self.form_fields_data) + 1
        field_id = (
            f"part_{field_num}"
            if field_type == "command_text"
            else f"field_{field_num}"
        )

        # Set appropriate defaults based on field type
        default: bool | int | str
        if field_type == "switch":
            default = False
        elif field_type == "slider":
            default = 50  # Numeric default for slider
        elif field_type == "color":
            default = "#000000"
        elif field_type == "date_time":
            default = ""
        elif field_type == "command_text":
            default = ""
        else:
            default = ""

        field_data: dict[str, Any] = {
            "type": field_type,
            "id": field_id,
            "template_key": "",
            "label": "" if field_type != "command_text" else _("Command"),
            "default": default,
            "placeholder": "",
            "tooltip": "",
            "command_flag": "",
            "off_value": "",
            "options": [],
        }

        # Add type-specific defaults
        if field_type == "slider":
            field_data["min_value"] = 0
            field_data["max_value"] = 100
            field_data["step"] = 1
        elif field_type == "text_area":
            field_data["rows"] = 4
        elif field_type == "date_time":
            field_data["format"] = "%Y-%m-%d %H:%M"
        elif field_type == "color":
            field_data["color_format"] = "hex"

        self.form_fields_data.append(field_data)

        row = self._create_field_row(field_data, len(self.form_fields_data) - 1)
        self.form_fields_list.append(row)

        self._update_preview()
        self._update_form_preview()

    def _create_field_row(self, field_data: Dict, index: int) -> Adw.ExpanderRow:
        """Create a collapsible UI row for a command part / form field."""
        type_icons = {
            "command_text": "💬",
            "text": "⌨️",
            "text_area": "📝",
            "password": "🔑",
            "switch": "🔘",
            "dropdown": "📋",
            "radio": "⚪",
            "multi_select": "☑️",
            "number": "🔢",
            "slider": "📊",
            "file_path": "📄",
            "directory_path": "📁",
            "date_time": "📅",
            "color": "🎨",
        }
        type_names = {
            "command_text": _("Command Text"),
            "text": _("Text Input"),
            "text_area": _("Text Area"),
            "password": _("Password"),
            "switch": _("Switch"),
            "dropdown": _("Dropdown"),
            "radio": _("Radio Buttons"),
            "multi_select": _("Multi-Select"),
            "number": _("Number"),
            "slider": _("Slider"),
            "file_path": _("File"),
            "directory_path": _("Directory"),
            "date_time": _("Date/Time"),
            "color": _("Color"),
        }
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
        """Convert a CommandFormField instance to a dictionary for UI representation."""
        field_data = {
            "type": self._field_type_to_string(field.field_type),
            "id": field.id,
            "template_key": field.template_key or field.id,
            "label": field.label,
            "default": field.default_value,
            "placeholder": field.placeholder,
            "tooltip": field.tooltip or "",
            "command_flag": field.command_flag or "",
            "off_value": field.off_value or "",
            "options": list(field.options) if field.options else [],
        }
        # Load extra_config fields
        extra = field.extra_config or {}
        keys = ["rows", "min_value", "max_value", "step", "format", "color_format"]
        for key in keys:
            if key in extra:
                field_data[key] = extra[key]
        return field_data

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

    def _parse_template_to_fields(self, template: str):
        """Parse a command template string into form fields data."""
        # For now, form_fields_data is already populated from stored form_fields
        # This method is a placeholder for future parsing logic
        pass

    def _field_type_to_string(self, field_type: FieldType) -> str:
        """Convert FieldType enum to string."""
        return {
            FieldType.TEXT: "text",
            FieldType.PASSWORD: "password",
            FieldType.TEXT_AREA: "text_area",
            FieldType.SWITCH: "switch",
            FieldType.DROPDOWN: "dropdown",
            FieldType.RADIO: "radio",
            FieldType.MULTI_SELECT: "multi_select",
            FieldType.NUMBER: "number",
            FieldType.SLIDER: "slider",
            FieldType.FILE_PATH: "file_path",
            FieldType.DIRECTORY_PATH: "directory_path",
            FieldType.DATE_TIME: "date_time",
            FieldType.COLOR: "color",
        }.get(field_type, "text")

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
        """Build command template from form fields data."""
        parts = []
        for field_data in self.form_fields_data:
            field_type = field_data.get("type", "text")
            if field_type == "command_text":
                value = field_data.get("default", "")
                if value:
                    parts.append(value)
            else:
                parts.append(f"{{{field_data.get('id', '')}}}")
        return " ".join(parts)

    def _get_field_extra_config(self, field_data: dict) -> dict:
        """Get extra configuration for special field types."""
        extra_config = {}
        ft = field_data.get("type", "text")
        if ft == "text_area":
            extra_config["rows"] = field_data.get("rows", 4)
        elif ft == "slider":
            extra_config["min_value"] = field_data.get("min_value", 0)
            extra_config["max_value"] = field_data.get("max_value", 100)
            extra_config["step"] = field_data.get("step", 1)
        elif ft == "date_time":
            extra_config["format"] = field_data.get("format", "%Y-%m-%d %H:%M")
        elif ft == "color":
            extra_config["color_format"] = field_data.get("color_format", "hex")
        return extra_config

    def _build_form_fields_list(self) -> list:
        """Build form fields list from form_fields_data."""
        field_type_map = {
            "text": FieldType.TEXT,
            "password": FieldType.PASSWORD,
            "text_area": FieldType.TEXT_AREA,
            "switch": FieldType.SWITCH,
            "dropdown": FieldType.DROPDOWN,
            "radio": FieldType.RADIO,
            "multi_select": FieldType.MULTI_SELECT,
            "number": FieldType.NUMBER,
            "slider": FieldType.SLIDER,
            "file_path": FieldType.FILE_PATH,
            "directory_path": FieldType.DIRECTORY_PATH,
            "date_time": FieldType.DATE_TIME,
            "color": FieldType.COLOR,
        }
        form_fields = []
        for i, field_data in enumerate(self.form_fields_data):
            if field_data.get("type") == "command_text":
                continue
            field_type = field_type_map.get(
                field_data.get("type", "text"), FieldType.TEXT
            )
            form_fields.append(
                CommandFormField(
                    id=field_data.get("id", f"field_{i}"),
                    label=field_data.get("label", ""),
                    field_type=field_type,
                    default_value=str(field_data.get("default", "")),
                    placeholder=field_data.get("placeholder", ""),
                    tooltip=field_data.get("tooltip", ""),
                    required=False,
                    command_flag=field_data.get("command_flag", ""),
                    off_value=field_data.get("off_value", ""),
                    options=field_data.get("options", []),
                    template_key=field_data.get("template_key", ""),
                    extra_config=self._get_field_extra_config(field_data),
                )
            )
        return form_fields

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

    def _add_cmd_text_rows(self, expander, field_data, type_icons, field_type):
        """Add rows for static command text."""
        text_row = Adw.EntryRow(title=_("Command text"))
        text_row.set_text(str(field_data.get("default", "")))

        def on_cmd_text_changed(r):
            val = r.get_text()
            field_data["default"] = val
            expander.set_title(
                f"{type_icons.get(field_type, '💬')} {val}"
                if val
                else f"{type_icons.get(field_type, '💬')} {_('(empty)')}"
            )
            self._update_preview()

        text_row.connect("changed", on_cmd_text_changed)
        expander.add_row(text_row)

    def _add_field_base_rows(self, expander, field_data, type_icons, field_type):
        """Add ID and Label rows to regular form fields."""
        id_row = Adw.EntryRow(title=_("ID"))
        id_row.set_text(field_data.get("id", ""))

        def on_id_changed(row):
            new_id = row.get_text()
            field_data["id"] = new_id
            expander.set_title(f"{type_icons.get(field_type, '📝')} {{{new_id}}}")
            self._update_preview()

        id_row.connect("changed", on_id_changed)
        expander.add_row(id_row)

        label_row = Adw.EntryRow(title=_("Label"))
        label_row.set_text(field_data.get("label", ""))

        def on_label_changed(r):
            field_data["label"] = r.get_text()
            self._update_preview()
            self._update_form_preview()

        label_row.connect("changed", on_label_changed)
        expander.add_row(label_row)

    def _add_text_rows(self, expander, field_data):
        placeholder_row = Adw.EntryRow(title=_("Placeholder"))
        placeholder_row.set_text(str(field_data.get("placeholder", "")))
        placeholder_row.connect(
            "changed",
            lambda r: (
                field_data.update({"placeholder": r.get_text()}),
                self._update_form_preview(),
            ),
        )
        expander.add_row(placeholder_row)

        default_row = Adw.EntryRow(title=_("Default"))
        default_row.set_text(str(field_data.get("default", "")))
        default_row.connect(
            "changed",
            lambda r: (
                field_data.update({"default": r.get_text()}),
                self._update_preview(),
                self._update_form_preview(),
            ),
        )
        expander.add_row(default_row)

    def _add_number_rows(self, expander, field_data):
        default_row = Adw.EntryRow(title=_("Default"))
        default_row.set_text(str(field_data.get("default", "")))
        default_row.connect(
            "changed",
            lambda r: (
                field_data.update({"default": r.get_text()}),
                self._update_preview(),
                self._update_form_preview(),
            ),
        )
        expander.add_row(default_row)

    def _add_switch_rows(self, expander, field_data):
        on_row = Adw.EntryRow(title=_("On value"))
        on_row.set_text(field_data.get("command_flag", ""))
        on_row.connect(
            "changed",
            lambda r: (
                field_data.update({"command_flag": r.get_text()}),
                self._update_preview(),
            ),
        )
        expander.add_row(on_row)

        off_row = Adw.EntryRow(title=_("Off value"))
        off_row.set_text(field_data.get("off_value", ""))
        off_row.connect(
            "changed",
            lambda r: (
                field_data.update({"off_value": r.get_text()}),
                self._update_preview(),
            ),
        )
        expander.add_row(off_row)

        default_row = Adw.SwitchRow(title=_("Default on"))
        default_row.set_active(bool(field_data.get("default", False)))
        default_row.connect(
            "notify::active",
            lambda r, _: (
                field_data.update({"default": r.get_active()}),
                self._update_form_preview(),
            ),
        )
        expander.add_row(default_row)

    def _add_dropdown_rows(self, expander, field_data):
        options_header = Adw.ActionRow(title=_("Options"))
        add_btn = Gtk.Button(
            icon_name="list-add-symbolic",
            css_classes=[BaseDialog.CSS_CLASS_FLAT, BaseDialog.CSS_CLASS_CIRCULAR],
            valign=Gtk.Align.CENTER,
        )
        a11y_label(add_btn, _("Add option"))
        options_header.add_suffix(add_btn)
        expander.add_row(options_header)

        listbox = Gtk.ListBox(
            selection_mode=Gtk.SelectionMode.NONE,
            css_classes=[BaseDialog.CSS_CLASS_BOXED_LIST],
        )
        box = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            margin_start=12,
            margin_end=12,
            margin_bottom=8,
        )
        box.append(listbox)
        expander.add_row(box)

        def sync_cb():
            self._sync_dropdown_options(listbox, field_data)

        def add_opt(val="", label=""):
            listbox.append(self._create_option_row(val, label, sync_cb, listbox))

        add_btn.connect("clicked", lambda _: (add_opt(), sync_cb()))

        for opt in field_data.get("options", []):
            if isinstance(opt, (tuple, list)) and len(opt) >= 2:
                add_opt(str(opt[0]), str(opt[1]))
            else:
                add_opt(str(opt), str(opt))

    def _sync_dropdown_options(self, listbox, field_data):
        """Sync dropdown options from UI to field data."""
        opts = []
        idx = 0
        while row := listbox.get_row_at_index(idx):
            if hasattr(row, "_val_entry") and hasattr(row, "_lbl_entry"):
                val, label = (
                    row._val_entry.get_text().strip(),
                    row._lbl_entry.get_text().strip(),
                )
                if val or label:
                    opts.append((val or label, label or val))
            idx += 1
        field_data["options"] = opts
        self._update_form_preview()

    def _create_option_row(self, value, label, sync_cb, listbox):
        row = Adw.ActionRow()
        ve = Gtk.Entry(
            placeholder_text=_("Value"), width_chars=10, valign=Gtk.Align.CENTER
        )
        ve.set_text(value)
        a11y_label(ve, _("Option value"))
        le = Gtk.Entry(
            placeholder_text=_("Label"),
            width_chars=15,
            valign=Gtk.Align.CENTER,
            hexpand=True,
        )
        le.set_text(label)
        a11y_label(le, _("Option label"))

        def move(delta):
            idx = row.get_index()
            new_idx = idx + delta
            if new_idx >= 0:
                listbox.remove(row)
                listbox.insert(row, new_idx)
                sync_cb()

        u = Gtk.Button(
            icon_name="go-up-symbolic",
            css_classes=[BaseDialog.CSS_CLASS_FLAT, BaseDialog.CSS_CLASS_CIRCULAR],
        )
        a11y_label(u, _("Move up"))
        d = Gtk.Button(
            icon_name="go-down-symbolic",
            css_classes=[BaseDialog.CSS_CLASS_FLAT, BaseDialog.CSS_CLASS_CIRCULAR],
        )
        a11y_label(d, _("Move down"))
        r = Gtk.Button(
            icon_name="user-trash-symbolic",
            css_classes=[
                BaseDialog.CSS_CLASS_FLAT,
                BaseDialog.CSS_CLASS_CIRCULAR,
                BaseDialog.CSS_CLASS_ERROR,
            ],
        )
        a11y_label(r, _("Remove option"))

        u.connect("clicked", lambda _: move(-1))
        d.connect("clicked", lambda _: move(1))
        r.connect("clicked", lambda _: (listbox.remove(row), sync_cb()))
        ve.connect("changed", lambda _: sync_cb())
        le.connect("changed", lambda _: sync_cb())

        row.add_prefix(ve)
        row.add_suffix(le)
        row.add_suffix(u)
        row.add_suffix(d)
        row.add_suffix(r)
        row._val_entry, row._lbl_entry = ve, le
        return row

    def _add_path_rows(self, expander, field_data):
        row = Adw.EntryRow(title=_("Default"))
        row.set_text(str(field_data.get("default", "")))
        row.connect(
            "changed",
            lambda r: (
                field_data.update({"default": r.get_text()}),
                self._update_preview(),
                self._update_form_preview(),
            ),
        )
        expander.add_row(row)

    def _add_password_rows(self, expander, field_data):
        row = Adw.EntryRow(title=_("Placeholder"))
        row.set_text(str(field_data.get("placeholder", "")))
        row.connect(
            "changed",
            lambda r: (
                field_data.update({"placeholder": r.get_text()}),
                self._update_form_preview(),
            ),
        )
        expander.add_row(row)

    def _add_textarea_rows(self, expander, field_data):
        self._add_text_rows(expander, field_data)
        row = Adw.SpinRow.new_with_range(2, 20, 1)
        row.set_title(_("Rows"))
        row.set_value(field_data.get("rows", 4))
        row.connect(
            BaseDialog.SIGNAL_NOTIFY_VALUE,
            lambda r, _: field_data.update({"rows": int(r.get_value())}),
        )
        expander.add_row(row)

    def _add_slider_rows(self, expander, field_data):
        def add_s(t, k, d):
            r = Adw.SpinRow.new_with_range(-9999, 9999, 1)
            r.set_title(t)
            r.set_value(float(field_data.get(k, d)))
            r.connect(
                BaseDialog.SIGNAL_NOTIFY_VALUE,
                lambda r, _: (
                    field_data.update({k: r.get_value()}),
                    self._update_form_preview(),
                ),
            )
            expander.add_row(r)

        add_s(_("Minimum"), "min_value", 0)
        add_s(_("Maximum"), "max_value", 100)
        add_s(_("Step"), "step", 1)
        dr = Adw.EntryRow(title=_("Default"))
        dr.set_text(str(field_data.get("default", "50")))
        dr.connect(
            "changed",
            lambda r: (
                field_data.update({"default": r.get_text()}),
                self._update_preview(),
                self._update_form_preview(),
            ),
        )
        expander.add_row(dr)

    def _add_datetime_rows(self, expander, field_data):
        r = Adw.EntryRow(title=_("Format"))
        r.set_text(str(field_data.get("format", "%Y-%m-%d %H:%M")))
        r.connect(
            "changed",
            lambda r: (
                field_data.update({"format": r.get_text()}),
                self._update_form_preview(),
            ),
        )
        expander.add_row(r)

    def _add_color_rows(self, expander, field_data):
        r = Adw.ComboRow(title=_("Format"))
        r.set_model(
            Gtk.StringList.new([_("Hex (#RRGGBB)"), _("RGB (R,G,B)"), _("None")])
        )
        r.set_selected(
            {"hex": 0, "rgb": 1}.get(field_data.get("color_format", "hex"), 2)
        )
        r.connect(
            "notify::selected",
            lambda r, _: (
                field_data.update(
                    {"color_format": ["hex", "rgb", "none"][r.get_selected()]}
                ),
                self._update_preview(),
            ),
        )
        expander.add_row(r)
        dr = Adw.EntryRow(title=_("Default Hex"))
        dr.set_text(str(field_data.get("default", "#ffffff")))
        dr.connect(
            "changed",
            lambda r: (
                field_data.update({"default": r.get_text()}),
                self._update_preview(),
                self._update_form_preview(),
            ),
        )
        expander.add_row(dr)
