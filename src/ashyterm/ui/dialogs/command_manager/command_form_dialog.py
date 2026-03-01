"""Command Form Dialog — form-based command parameter entry."""

from typing import Any, Dict, Optional

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, GObject, Gtk, Pango

from ....data.command_manager_models import (
    CommandButton,
    CommandFormField,
    FieldType,
    get_command_button_manager,
)
from ....settings.manager import SettingsManager
from ....utils.syntax_utils import get_bash_pango_markup
from ....utils.tooltip_helper import get_tooltip_helper
from ....utils.translation_utils import _
from ...widgets.form_widget_builder import create_field_from_form_field
from ..base_dialog import BaseDialog
from ._constants import EXTRACT_COMMANDS


class CommandFormDialog(Adw.Window):
    __gsignals__ = {
        "command-ready": (
            GObject.SignalFlags.RUN_FIRST,
            None,
            (GObject.TYPE_STRING, GObject.TYPE_BOOLEAN, GObject.TYPE_BOOLEAN),
        ),  # (command, execute, send_to_all)
    }

    DIALOG_HEIGHT_MIN = 350
    DIALOG_HEIGHT_MAX = 800

    def __init__(
        self,
        parent,
        command: CommandButton,
        send_to_all: bool = False,
        settings_manager: Optional[SettingsManager] = None,
    ):
        self._command_manager = get_command_button_manager()
        self._settings_manager = settings_manager
        saved_width = self._command_manager.get_command_pref(command.id, "dialog_width")
        saved_height = self._command_manager.get_command_pref(
            command.id, "dialog_height"
        )

        if saved_width and saved_height:
            dialog_width = saved_width
            dialog_height = saved_height
        else:
            dialog_width = 550
            dialog_height = self._calculate_dialog_height(command)

        super().__init__(
            transient_for=parent,
            modal=True,
            default_width=dialog_width,
            default_height=dialog_height,
        )
        self.add_css_class("ashyterm-dialog")
        self.add_css_class("command-form-dialog")
        self.command = command

        # Use the passed send_to_all value (from context menu or default)
        self.send_to_all = send_to_all

        self.field_widgets: Dict[str, Gtk.Widget] = {}

        self.set_title(command.name)
        self._build_ui()
        self._apply_color_scheme()

        # Keyboard handler
        key_controller = Gtk.EventControllerKey.new()
        key_controller.connect("key-pressed", self._on_key_pressed)
        self.add_controller(key_controller)

        # Save size when dialog is closed
        self.connect("close-request", self._on_close_save_size)

    def _apply_color_scheme(self):
        """Dialog theming is handled globally via the ashyterm-dialog class.

        This method is kept for potential future customization needs but
        currently relies on global CSS from apply_gtk_terminal_theme.
        """
        pass

    def _on_close_save_size(self, widget):
        """Save dialog size when closing."""
        width = self.get_width()
        height = self.get_height()

        # Only save if size is reasonable
        if width > 300 and height > 200:
            self._command_manager.set_command_pref(
                self.command.id, "dialog_width", width
            )
            self._command_manager.set_command_pref(
                self.command.id, "dialog_height", height
            )

        return False  # Allow close to proceed

    @classmethod
    def _calculate_dialog_height(cls, command: CommandButton) -> int:
        """Calculate optimal dialog height based on field types and count."""
        base_height = 180  # Header + preview + margins

        if not command.form_fields:
            return base_height + 50

        # Height estimates per field type
        height_map = {
            FieldType.TEXT: 56,
            FieldType.PASSWORD: 56,
            FieldType.TEXT_AREA: 120,
            FieldType.SWITCH: 50,
            FieldType.DROPDOWN: 56,
            FieldType.NUMBER: 56,
            FieldType.SLIDER: 70,
            FieldType.FILE_PATH: 56,
            FieldType.DIRECTORY_PATH: 56,
            FieldType.DATE_TIME: 56,
            FieldType.COLOR: 56,
        }

        fields_height = 50  # Group title
        for field in command.form_fields:
            if field.field_type in [FieldType.RADIO, FieldType.MULTI_SELECT]:
                num_options = len(field.options) if field.options else 2
                fields_height += 50 + (num_options * 36)
            else:
                fields_height += height_map.get(field.field_type, 56)

        # Add description height if present
        if command.description:
            fields_height += 40

        # Clamp between reasonable bounds
        total = base_height + fields_height
        return max(350, min(total, 800))

    def _on_key_pressed(self, controller, keyval, _keycode, state):
        if keyval == Gdk.KEY_Escape:
            self.close()
            return Gdk.EVENT_STOP
        elif keyval == Gdk.KEY_Return and (state & Gdk.ModifierType.CONTROL_MASK):
            self._on_execute_clicked(None)
            return Gdk.EVENT_STOP
        return Gdk.EVENT_PROPAGATE

    def _build_ui(self):
        # Header bar
        header = Adw.HeaderBar()

        cancel_button = Gtk.Button(label=_("Cancel"))
        cancel_button.connect("clicked", lambda _: self.close())
        header.pack_start(cancel_button)

        # Action buttons
        button_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)

        insert_button = Gtk.Button(label=_("Insert"))
        get_tooltip_helper().add_tooltip(
            insert_button, _("Insert command into terminal without executing")
        )
        insert_button.connect("clicked", self._on_insert_clicked)
        button_box.append(insert_button)

        execute_button = Gtk.Button(
            label=_("Execute"), css_classes=[BaseDialog.CSS_CLASS_SUGGESTED]
        )
        get_tooltip_helper().add_tooltip(
            execute_button, _("Insert and execute command (Ctrl+Enter)")
        )
        execute_button.connect("clicked", self._on_execute_clicked)
        button_box.append(execute_button)

        header.pack_end(button_box)

        # Content area
        content_box = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=12,
            margin_top=12,
            margin_bottom=12,
            margin_start=16,
            margin_end=16,
        )

        # Command Preview at top - with title and compact display
        preview_container = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=4,
        )

        preview_title = Gtk.Label(
            label=_("Command Preview"),
            xalign=0.0,
            css_classes=[BaseDialog.CSS_CLASS_DIM_LABEL, "caption"],
        )
        preview_container.append(preview_title)

        preview_box = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL,
            spacing=0,
            css_classes=["card", "command-preview"],
        )

        self.preview_label = Gtk.Label(
            label="",
            use_markup=True,
            wrap=True,
            wrap_mode=Pango.WrapMode.CHAR,
            xalign=0.0,
            hexpand=True,
            selectable=False,  # Don't select on start
            css_classes=["monospace"],
            margin_start=10,
            margin_end=10,
            margin_top=5,
            margin_bottom=5,
        )
        preview_box.append(self.preview_label)
        preview_container.append(preview_box)

        content_box.append(preview_container)

        # Description
        if self.command.description:
            desc_label = Gtk.Label(
                label=self.command.description,
                wrap=True,
                wrap_mode=Pango.WrapMode.WORD_CHAR,
                xalign=0.0,
                css_classes=[BaseDialog.CSS_CLASS_DIM_LABEL],
            )
            content_box.append(desc_label)

        # Form fields - no extra title header to save space
        if self.command.form_fields:
            form_group = Adw.PreferencesGroup()

            for form_field in self.command.form_fields:
                row = self._create_field_row(form_field)
                if row:
                    form_group.add(row)

            content_box.append(form_group)

        # Scrolled window for content
        scrolled = Gtk.ScrolledWindow(
            hscrollbar_policy=Gtk.PolicyType.NEVER,
            min_content_height=200,
            max_content_height=500,
        )
        scrolled.set_child(content_box)

        # Assemble
        toolbar_view = Adw.ToolbarView()
        toolbar_view.add_top_bar(header)
        toolbar_view.set_content(scrolled)
        self.set_content(toolbar_view)

        # Update preview initially
        self._update_preview()

    def _create_field_row(self, form_field: CommandFormField) -> Optional[Gtk.Widget]:
        """Create the appropriate row widget for a form field.

        Delegates to FormWidgetBuilder for widget creation.
        """
        # Use the centralized form widget builder
        row, value_widget = create_field_from_form_field(
            form_field, on_change=self._update_preview
        )

        # Store value widget for later retrieval
        self.field_widgets[form_field.id] = value_widget

        # Special handling for file/directory path browse buttons
        if form_field.field_type in (FieldType.FILE_PATH, FieldType.DIRECTORY_PATH):
            if hasattr(value_widget, "_browse_button"):
                value_widget._browse_button.connect(
                    "clicked",
                    self._on_browse_clicked,
                    value_widget,
                    form_field.field_type,
                )

        return row

    def _on_browse_clicked(self, button, entry, field_type):
        """Open file/directory chooser dialog."""
        if field_type == FieldType.DIRECTORY_PATH:
            action = Gtk.FileChooserAction.SELECT_FOLDER
        else:
            action = Gtk.FileChooserAction.OPEN

        dialog = Gtk.FileChooserNative.new(
            _("Select Path"),
            self,
            action,
            _("Select"),
            _("Cancel"),
        )
        dialog.connect("response", self._on_file_chooser_response, entry)
        dialog.show()

    def _on_file_chooser_response(self, dialog, response, entry):
        if response == Gtk.ResponseType.ACCEPT:
            file = dialog.get_file()
            if file:
                entry.set_text(file.get_path())

    def _get_field_values(self) -> Dict[str, Any]:
        """Collect current values from all form fields."""
        values = {}

        for form_field in self.command.form_fields:
            if widget := self.field_widgets.get(form_field.id):
                values[form_field.id] = self._extract_field_value(form_field, widget)

        return values

    def _extract_field_value(self, form_field, widget) -> Any:
        """Extract value from a specific field widget based on its type."""
        ft = form_field.field_type

        # Use a mapping for extraction methods to reduce if/elif chains
        extractors = {
            FieldType.SWITCH: lambda w: w.get_active(),
            FieldType.DROPDOWN: self._extract_dropdown_value,
            FieldType.RADIO: lambda w: getattr(w, "_selected_value", ""),
            FieldType.MULTI_SELECT: self._extract_multi_select_value,
            FieldType.TEXT_AREA: self._extract_textarea_value,
            FieldType.SLIDER: lambda w: str(int(w.get_value())),
            FieldType.COLOR: self._extract_color_value,
        }

        if ft in extractors:
            return extractors[ft](widget)

        # Common text-based fields (TEXT, NUMBER, PASSWORD, FILE_PATH, etc.)
        if hasattr(widget, "get_text"):
            return widget.get_text()

        return ""

    def _extract_dropdown_value(self, widget) -> str:
        idx = widget.get_selected()
        if idx >= 0 and hasattr(widget, "_options"):
            return widget._options[idx][0]
        return ""

    def _extract_multi_select_value(self, widget) -> str:
        selected = []
        if hasattr(widget, "_checkboxes"):
            for check in widget._checkboxes:
                if check.get_active() and hasattr(check, "_value"):
                    selected.append(check._value)
        return " ".join(selected)

    def _extract_textarea_value(self, widget) -> str:
        buffer = widget.get_buffer()
        return buffer.get_text(buffer.get_start_iter(), buffer.get_end_iter(), False)

    def _extract_color_value(self, widget) -> str:
        rgba = widget.get_rgba()
        color_format = getattr(widget, "_color_format", "hex")
        r, g, b = (int(rgba.red * 255), int(rgba.green * 255), int(rgba.blue * 255))

        if color_format == "rgb":
            return f"{r},{g},{b}"
        return f"#{r:02x}{g:02x}{b:02x}"

    def _update_preview(self):
        """Update the command preview based on current field values."""
        values = self._get_field_values()

        # Build command with special handling for find command
        command = self._build_command_from_values(values)
        # Apply syntax highlighting with terminal color scheme
        palette = None
        fg_color = "#ffffff"
        if (
            self._settings_manager
            and self._settings_manager.get("gtk_theme", "") == "terminal"
        ):
            scheme = self._settings_manager.get_color_scheme_data()
            palette = scheme.get("palette", [])
            fg_color = scheme.get("foreground", "#ffffff")
        highlighted = get_bash_pango_markup(command, palette, fg_color)
        self.preview_label.set_markup(highlighted)

    def _build_command_from_values(self, values: Dict[str, Any]) -> str:
        """Build the command string from form values with proper flag handling."""
        # Handle special commands
        if self.command.id == "builtin_find":
            return self._build_find_command(values)
        elif self.command.id == "builtin_compress":
            return self._build_compress_command(values)
        elif self.command.id == "builtin_extract":
            return self._build_extract_command(values)
        elif self.command.id == "builtin_systemctl":
            return self._build_systemctl_command(values)
        elif self.command.id == "builtin_journalctl":
            return self._build_journalctl_command(values)
        elif self.command.id == "builtin_pacman":
            return self._build_pacman_command(values)

        # Generic handling for other commands
        return self.command.build_command(values)

    def _build_find_date_filter(self, values: Dict[str, Any]) -> Optional[str]:
        """Build date filter part for find command."""
        date_value = values.get("date_value", "").strip()
        if not date_value:
            return None
        try:
            value = int(date_value)
            if value <= 0:
                return None
            date_unit = values.get("date_unit", "days")
            if date_unit == "minutes":
                return f"-mmin -{value}"
            elif date_unit == "hours":
                return f"-mmin -{value * 60}"
            return f"-mtime -{value}"
        except (ValueError, TypeError):
            return None

    def _build_find_command(self, values: Dict[str, Any]) -> str:
        """Build the find command with proper flag handling."""
        parts = ["find"]

        path = values.get("path", ".").strip() or "."
        parts.append(path)

        name_pattern = values.get("name_pattern", "").strip()
        if name_pattern:
            parts.append(f"-name '{name_pattern}'")

        if not values.get("recursive", True):
            parts.append("-maxdepth 1")

        file_type = values.get("file_type", "").strip()
        if file_type:
            parts.append(file_type)

        size_filter = values.get("size_filter", "").strip()
        if size_filter:
            parts.append(f"-size {size_filter}")

        date_filter = self._build_find_date_filter(values)
        if date_filter:
            parts.append(date_filter)

        grep_pattern = values.get("grep_pattern", "").strip()
        if grep_pattern:
            parts.append(f"-exec grep -l '{grep_pattern}' {{}} \\\\;")

        return " ".join(parts)

    def _build_compress_command(self, values: Dict[str, Any]) -> str:
        """Build compression command based on format."""
        input_path = values.get("input", "").strip()
        output_path = values.get("output", "").strip()
        archive_format = values.get("format", "tar.xz")

        # Use placeholder if no input specified
        input_display = input_path if input_path else "<files>"

        # Generate default output name based on format if not specified
        if not output_path:
            output_path = f"archive.{archive_format}"

        if archive_format == "zip":
            return f"zip -r {output_path} {input_display}"
        elif archive_format == "tar":
            return f"tar -cvf {output_path} {input_display}"
        elif archive_format == "tar.gz":
            return f"tar -czvf {output_path} {input_display}"
        elif archive_format == "tar.bz2":
            return f"tar -cjvf {output_path} {input_display}"
        elif archive_format == "tar.xz":
            return f"tar -cJvf {output_path} {input_display}"
        elif archive_format == "tar.zst":
            return f"tar -cvf - {input_display} | zstd -o {output_path}"
        elif archive_format == "tar.lzma":
            return f"tar -cvf - {input_display} | lzma -c > {output_path}"
        else:
            return f"tar -cJvf {output_path} {input_display}"

    def _build_extract_command(self, values: Dict[str, Any]) -> str:
        """Build extraction command based on archive file extension."""
        input_path = values.get("input", "").strip()
        output_path = values.get("output", "").strip()

        # Use placeholder if no input
        input_display = input_path if input_path else "<archive>"

        # Find matching extraction command
        for (
            extensions,
            uses_dest_flag,
            template_with_output,
            template_without,
        ) in EXTRACT_COMMANDS:
            if any(input_path.endswith(ext) for ext in extensions):
                return self._format_extract_template(
                    input_display,
                    output_path,
                    uses_dest_flag,
                    template_with_output,
                    template_without,
                )

        # Default to tar with auto-detection
        dest_flag = f"-C {output_path}" if output_path else ""
        return f"tar -xvf {input_display} {dest_flag}".strip()

    def _format_extract_template(
        self,
        input_display: str,
        output_path: str,
        uses_dest_flag: bool,
        template_with_output: str,
        template_without: str,
    ) -> str:
        """Format extraction command template with input/output values.

        Args:
            input_display: Input file path or placeholder.
            output_path: Output directory path.
            uses_dest_flag: Whether to use -C flag format.
            template_with_output: Template when output path is provided.
            template_without: Template when no output path.

        Returns:
            Formatted command string.
        """
        if output_path:
            dest_flag = f"-C {output_path}" if uses_dest_flag else ""
            return template_with_output.format(
                input=input_display, dest=dest_flag, output=output_path
            ).strip()
        return template_without.format(input=input_display)

    def _build_systemctl_command(self, values: Dict[str, Any]) -> str:
        """Build systemctl command with proper handling of service name."""
        parts = ["systemctl"]

        # User scope flag
        if values.get("user_scope", False):
            parts.append("--user")

        action = values.get("action", "status").strip()
        service = values.get("service", "").strip()

        # Some actions don't need a service name
        list_actions = [
            "list-units --type=service",
            "list-units --type=service --state=running",
            "list-units --type=service --state=failed",
        ]

        parts.append(action)

        if action not in list_actions and service:
            parts.append(service)

        return " ".join(parts)

    def _build_journalctl_command(self, values: Dict[str, Any]) -> str:
        """Build journalctl command with proper flag handling."""
        parts = ["journalctl"]

        # Unit filter
        unit = values.get("unit", "").strip()
        if unit:
            parts.append(f"-u {unit}")

        # Follow flag
        if values.get("follow", False):
            parts.append("-f")

        # Number of lines
        lines = values.get("lines", "").strip()
        if lines:
            try:
                n = int(lines)
                if n > 0:
                    parts.append(f"-n {n}")
            except (ValueError, TypeError):
                pass

        # Priority filter
        priority = values.get("priority", "").strip()
        if priority:
            parts.append(priority)

        # Since filter
        since = values.get("since", "").strip()
        if since:
            parts.append(since)

        return " ".join(parts)

    def _build_pacman_command(self, values: Dict[str, Any]) -> str:
        """Build pacman command with proper handling of package name."""
        action = values.get("action", "-S").strip()
        package = values.get("package", "").strip()

        # Actions that don't require a package name
        no_pkg_actions = ["-Syu", "-Syyu", "-Sc", "-Scc", "-Q", "-Qe", "-Qdt"]

        if action in no_pkg_actions:
            return f"sudo pacman {action}"
        elif package:
            return f"sudo pacman {action} {package}"
        else:
            # Show command template even without package
            return f"sudo pacman {action}"

    def _on_insert_clicked(self, button):
        """Insert command without executing."""
        values = self._get_field_values()
        command = self._build_command_from_values(values)
        self.emit("command-ready", command, False, self.send_to_all)
        self.close()

    def _on_execute_clicked(self, button):
        """Insert and execute command."""
        values = self._get_field_values()
        command = self._build_command_from_values(values)
        self.emit("command-ready", command, True, self.send_to_all)
        self.close()
