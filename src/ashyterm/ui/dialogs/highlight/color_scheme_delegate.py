"""Color scheme management delegate for HighlightDialog."""

from typing import TYPE_CHECKING

from gi.repository import Adw, Gtk

from ....helpers import generate_unique_name
from ....settings.manager import get_settings_manager
from ....utils.accessibility import set_label as a11y_label
from ....utils.translation_utils import _
from ..base_dialog import BaseDialog, create_icon_button

if TYPE_CHECKING:
    from .highlight_dialog import HighlightDialog


class ColorSchemeDelegate:
    """Manages the Terminal Colors page with color scheme selection and editing."""

    def __init__(self, dialog: "HighlightDialog") -> None:
        self.dlg = dialog

    def setup_page(self, page: Adw.PreferencesPage) -> None:
        """Setup the Terminal Colors page with integrated Color Scheme selector."""
        scheme_group = Adw.PreferencesGroup(
            title=_("Color Scheme"),
        )
        page.add(scheme_group)

        self.dlg._scheme_listbox = Gtk.ListBox()
        self.dlg._scheme_listbox.set_selection_mode(Gtk.SelectionMode.SINGLE)
        self.dlg._scheme_listbox.add_css_class("boxed-list")
        self.dlg._scheme_listbox.connect(
            "row-selected", self._on_scheme_row_selected
        )

        self.dlg._scheme_rows = {}
        self.populate_color_schemes()

        scheme_group.add(self.dlg._scheme_listbox)

        actions_group = Adw.PreferencesGroup()
        page.add(actions_group)

        new_row = Adw.ActionRow(
            title=_("Create New Scheme"),
            subtitle=_("Create a custom color scheme based on existing"),
        )
        new_row.set_activatable(True)
        new_btn = create_icon_button(
            "list-add-symbolic",
            on_clicked=self._on_new_scheme_clicked,
            flat=True,
            valign=Gtk.Align.CENTER,
        )
        new_row.add_suffix(new_btn)
        new_row.set_activatable_widget(new_btn)
        actions_group.add(new_row)

    def populate_color_schemes(self) -> None:
        """Populate the color scheme list."""
        settings = get_settings_manager()
        all_schemes = settings.get_all_schemes()
        scheme_order = settings.get_scheme_order()
        current_scheme = settings.get_color_scheme_name()

        while True:
            row = self.dlg._scheme_listbox.get_first_child()
            if row is None:
                break
            self.dlg._scheme_listbox.remove(row)
        self.dlg._scheme_rows.clear()

        for scheme_key in scheme_order:
            if scheme_key not in all_schemes:
                continue

            scheme_data = all_schemes[scheme_key]
            is_custom = scheme_key in settings.custom_schemes

            row = self._create_scheme_row(scheme_key, scheme_data, is_custom)
            self.dlg._scheme_listbox.append(row)
            self.dlg._scheme_rows[scheme_key] = row

            if scheme_key == current_scheme:
                self.dlg._scheme_listbox.select_row(row)

    def _create_scheme_row(
        self, scheme_key: str, scheme_data: dict, is_custom: bool
    ) -> Adw.ActionRow:
        """Create a row for a color scheme with preview."""
        row = Adw.ActionRow(
            title=scheme_data.get("name", scheme_key),
        )
        row.scheme_key = scheme_key
        row.scheme_data = scheme_data
        row.is_custom = is_custom

        preview = Gtk.DrawingArea()
        preview.set_size_request(120, 32)
        preview.set_valign(Gtk.Align.CENTER)
        preview.set_margin_end(12)
        a11y_label(preview, _("Color scheme preview"))

        def draw_preview(area, cr, width, height):
            bg_color = scheme_data.get("background", "#000000")
            self._set_color_from_hex(cr, bg_color)
            cr.rectangle(0, 0, width * 0.3, height)
            cr.fill()

            fg_color = scheme_data.get("foreground", "#ffffff")
            self._set_color_from_hex(cr, fg_color)
            cr.rectangle(width * 0.3, 0, width * 0.15, height)
            cr.fill()

            palette = scheme_data.get("palette", [])
            num_colors = min(len(palette), 8)
            if num_colors > 0:
                color_width = (width * 0.55) / num_colors
                x_offset = width * 0.45
                for i, color in enumerate(palette[:num_colors]):
                    self._set_color_from_hex(cr, color)
                    cr.rectangle(x_offset + i * color_width, 0, color_width, height)
                    cr.fill()

            cr.set_source_rgba(0.5, 0.5, 0.5, 0.3)
            cr.set_line_width(1)
            cr.rectangle(0.5, 0.5, width - 1, height - 1)
            cr.stroke()

        preview.set_draw_func(draw_preview)
        row.add_prefix(preview)

        edit_tooltip = (
            _("Edit scheme") if is_custom else _("Customize (creates a copy)")
        )
        edit_btn = create_icon_button(
            "document-edit-symbolic",
            tooltip=edit_tooltip,
            on_clicked=lambda b, r=row: self._on_edit_scheme_clicked(r),
            flat=True,
            valign=Gtk.Align.CENTER,
        )
        row.add_suffix(edit_btn)

        if is_custom:
            delete_btn = create_icon_button(
                "user-trash-symbolic",
                tooltip=_("Delete scheme"),
                on_clicked=lambda b, r=row: self._on_delete_scheme_clicked(r),
                flat=True,
                valign=Gtk.Align.CENTER,
            )
            row.add_suffix(delete_btn)

        check_icon = Gtk.Image.new_from_icon_name("emblem-ok-symbolic")
        check_icon.set_visible(False)
        row.check_icon = check_icon
        row.add_suffix(check_icon)

        return row

    def _set_color_from_hex(self, cr, hex_color: str) -> None:
        """Set cairo source color from hex string."""
        try:
            hex_val = hex_color.lstrip("#")
            r = int(hex_val[0:2], 16) / 255.0
            g = int(hex_val[2:4], 16) / 255.0
            b = int(hex_val[4:6], 16) / 255.0
            cr.set_source_rgb(r, g, b)
        except (ValueError, IndexError):
            cr.set_source_rgb(0.5, 0.5, 0.5)

    def _on_scheme_row_selected(self, listbox, row) -> None:
        """Handle color scheme selection."""
        if row is None:
            return

        if self.dlg._initializing:
            return

        for scheme_row in self.dlg._scheme_rows.values():
            scheme_row.check_icon.set_visible(scheme_row == row)

        settings = get_settings_manager()
        scheme_order = settings.get_scheme_order()
        selected_index = scheme_order.index(row.scheme_key)
        settings.set("color_scheme", selected_index)

        self.dlg.logger.info(f"Color scheme changed to: {row.scheme_key}")

        if self.dlg._parent_window and hasattr(
            self.dlg._parent_window, "terminal_manager"
        ):
            self.dlg._parent_window.terminal_manager.apply_settings_to_all_terminals()

        try:
            from ....terminal.highlighter import get_shell_input_highlighter

            highlighter = get_shell_input_highlighter()
            highlighter.refresh_settings()
        except Exception as e:
            self.dlg.logger.warning(
                f"Failed to refresh shell input highlighter: {e}"
            )

    def _on_new_scheme_clicked(self, button) -> None:
        """Create a new color scheme based on selected."""
        from ...color_scheme_dialog import _SchemeEditorDialog

        settings = get_settings_manager()
        selected_row = self.dlg._scheme_listbox.get_selected_row()
        template_scheme = (
            selected_row.scheme_data
            if selected_row
            else settings.get_all_schemes()["dark"]
        )

        all_names = {s["name"] for s in settings.get_all_schemes().values()}
        new_name = generate_unique_name(
            f"Copy of {template_scheme['name']}", all_names
        )

        new_scheme_data = template_scheme.copy()
        new_scheme_data["name"] = new_name

        editor = _SchemeEditorDialog(
            self.dlg, settings, new_name, new_scheme_data, is_new=True
        )
        editor.connect("save-requested", self._on_editor_save)
        editor.present()

    def _on_edit_scheme_clicked(self, row) -> None:
        """Edit a color scheme. Built-in schemes create a copy when saved."""
        from ...color_scheme_dialog import _SchemeEditorDialog

        settings = get_settings_manager()
        is_builtin = not row.is_custom

        if is_builtin:
            all_names = {s["name"] for s in settings.get_all_schemes().values()}
            new_name = generate_unique_name(
                f"{row.scheme_data.get('name', row.scheme_key)} (Custom)",
                all_names,
            )
            scheme_data = row.scheme_data.copy()
            scheme_data["name"] = new_name
            editor = _SchemeEditorDialog(
                self.dlg, settings, None, scheme_data, is_new=True
            )
        else:
            editor = _SchemeEditorDialog(
                self.dlg,
                settings,
                row.scheme_key,
                row.scheme_data.copy(),
                is_new=False,
            )

        editor.connect("save-requested", self._on_editor_save)
        editor.present()

    def _on_delete_scheme_clicked(self, row) -> None:
        """Delete a custom color scheme."""
        from ..base_dialog import show_delete_confirmation_dialog

        scheme_key = row.scheme_key
        scheme_name = row.scheme_data.get("name", scheme_key)

        def on_confirm() -> None:
            settings = get_settings_manager()
            if scheme_key in settings.custom_schemes:
                del settings.custom_schemes[scheme_key]
                settings.save_custom_schemes()

                if settings.get_color_scheme_name() == scheme_key:
                    settings.set("color_scheme", 0)
                    if self.dlg._parent_window and hasattr(
                        self.dlg._parent_window, "terminal_manager"
                    ):
                        self.dlg._parent_window.terminal_manager.apply_settings_to_all_terminals()

                self.populate_color_schemes()
                self.dlg.add_toast(Adw.Toast(title=_("Scheme deleted")))

        show_delete_confirmation_dialog(
            parent=self.dlg,
            heading=_("Delete Scheme?"),
            body=BaseDialog.MSG_DELETE_CONFIRMATION.format(scheme_name),
            on_confirm=on_confirm,
        )

    def _save_scheme_data(
        self, original_key: str, new_key: str, scheme_data: dict
    ) -> tuple[bool, str]:
        """Save scheme data and return (is_new, final_key)."""
        settings = get_settings_manager()
        is_new = (
            original_key is None
            or original_key == ""
            or original_key not in settings.custom_schemes
        )

        if is_new:
            import time

            unique_key = f"custom_{int(time.time() * 1000)}"
            settings.custom_schemes[unique_key] = scheme_data
            return True, unique_key

        if original_key in settings.custom_schemes:
            del settings.custom_schemes[original_key]
        final_key = new_key if new_key else original_key
        settings.custom_schemes[final_key] = scheme_data
        return False, final_key

    def _select_scheme_by_key(self, scheme_key: str) -> None:
        """Select a scheme in the listbox by its key."""
        settings = get_settings_manager()
        scheme_order = settings.get_scheme_order()
        try:
            new_scheme_index = scheme_order.index(scheme_key)
            settings.set("color_scheme", new_scheme_index)
            for scheme_row in self.dlg._scheme_rows.values():
                if scheme_row.scheme_key == scheme_key:
                    self.dlg._scheme_listbox.select_row(scheme_row)
                    for other_row in self.dlg._scheme_rows.values():
                        other_row.check_icon.set_visible(other_row == scheme_row)
                    break
        except ValueError:
            pass

    def _apply_scheme_changes(self) -> None:
        """Apply scheme changes to terminals and GTK theme."""
        try:
            from ....terminal.highlighter import get_shell_input_highlighter

            highlighter = get_shell_input_highlighter()
            highlighter.refresh_settings()
        except Exception:
            pass

    def _on_editor_save(
        self, editor, original_key: str, new_key: str, scheme_data: dict
    ) -> None:
        """Handle save from scheme editor."""
        settings = get_settings_manager()

        is_new, unique_key = self._save_scheme_data(
            original_key, new_key, scheme_data
        )
        settings.save_custom_schemes()
        self.populate_color_schemes()

        if is_new:
            self._select_scheme_by_key(unique_key)

        self._apply_scheme_changes()
        self.dlg.add_toast(Adw.Toast(title=_("Scheme saved")))
