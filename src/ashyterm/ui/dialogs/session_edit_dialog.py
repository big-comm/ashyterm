# ashyterm/ui/dialogs/session_edit_dialog.py

import copy
from pathlib import Path
from typing import Optional

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gio, GLib, Gtk

from ...sessions.models import SessionItem
from ...utils.exceptions import SSHKeyError
from ...utils.platform import get_ssh_directory
from ...utils.security import (
    HostnameValidator,
    validate_ssh_key_file,
)
from ...utils.tooltip_helper import get_tooltip_helper
from ...utils.translation_utils import _
from ...utils.accessibility import set_label as a11y_label
from ..widgets.bash_text_view import BashTextView
from .base_dialog import BaseDialog
from .session_edit_form import (
    SessionFormCollector,
    selected_to_tri_state,
    tri_state_to_selected,
)
from .session_edit_sections import (
    add_folder_expander as _add_folder_expander_impl,
    add_highlighting_expander as _add_highlighting_expander_impl,
    create_local_terminal_section as _create_local_terminal_section_impl,
    create_ssh_options_group as _create_ssh_options_group_impl,
    create_ssh_section as _create_ssh_section_impl,
    create_tristate_combo_row as _create_tristate_combo_row_impl,
)
from .session_edit_visibility import (
    update_auth_visibility as _update_auth_visibility_impl,
    update_local_visibility as _update_local_visibility_impl,
    update_port_forward_state as _update_port_forward_state_impl,
    update_post_login_command_state as _update_post_login_command_state_impl,
    update_sftp_state as _update_sftp_state_impl,
    update_ssh_visibility as _update_ssh_visibility_impl,
)
from .session_edit_validators import (
    validate_basic,
    validate_hostname,
    validate_local,
    validate_port_forward,
    validate_post_login,
    validate_sftp_directory,
    validate_ssh_bundle,
    validate_ssh_key,
)


class SessionEditDialog(BaseDialog):
    def __init__(
        self,
        parent_window,
        session_item: SessionItem,
        session_store,
        position: int,
        folder_store=None,
        settings_manager=None,
    ):
        self.is_new_item = position == -1
        title = _("Add Session") if self.is_new_item else _("Edit Session")
        super().__init__(parent_window, title, default_width=700, default_height=720)

        self.session_store = session_store
        self.folder_store = folder_store
        self.position = position
        self._settings_manager = settings_manager
        # The editing_session is a data holder, not the live store object
        self.editing_session = (
            SessionItem.from_dict(session_item.to_dict())
            if not self.is_new_item
            else session_item
        )
        self.original_session = session_item if not self.is_new_item else None
        self.folder_paths_map: dict[str, str] = {}
        self.post_login_expander: Optional[Adw.ExpanderRow] = None
        self.post_login_switch = None  # Alias to expander for compatibility
        self.post_login_entry: Optional[BashTextView] = None
        self.post_login_text_view: Optional[BashTextView] = None
        self.sftp_group: Optional[Adw.PreferencesGroup] = None
        self.sftp_switch: Optional[Adw.SwitchRow] = None
        self.sftp_local_entry: Optional[Adw.EntryRow] = None
        self.sftp_remote_entry: Optional[Adw.EntryRow] = None
        self.port_forward_group: Optional[Adw.PreferencesGroup] = None
        self.port_forward_list: Optional[Gtk.ListBox] = None
        self.port_forward_add_button: Optional[Gtk.Button] = None
        self.port_forwardings: list[dict] = [
            dict(item) for item in self.editing_session.port_forwardings
        ]
        self.x11_switch: Optional[Adw.SwitchRow] = None
        # SSH options
        self.ssh_options_group: Optional[Adw.PreferencesGroup] = None
        self.post_login_command_group: Optional[Adw.PreferencesGroup] = None
        self.post_login_command_container: Optional[Gtk.Box] = None
        self.test_button: Optional[Gtk.Button] = None
        self.sftp_local_row: Optional[Adw.ActionRow] = None
        self.sftp_remote_row: Optional[Adw.ActionRow] = None
        self.port_forward_add_row: Optional[Adw.ActionRow] = None
        self.port_forward_list_row: Optional[Adw.ActionRow] = None
        self.folder_combo: Optional[Adw.ComboRow] = None
        self.local_working_dir_entry: Gtk.Entry = None  # type: ignore[assignment]
        self.local_startup_command_view: Optional[BashTextView] = None
        self.testing_dialog: Optional[Gtk.Window] = None
        # Local terminal options
        self.local_terminal_group: Optional[Adw.PreferencesGroup] = None
        self.startup_commands_group: Optional[Adw.PreferencesGroup] = None
        self._updating_highlighting_ui = False
        self.form_collector = SessionFormCollector(self)
        self._setup_ui()
        self.connect("map", self._on_map)
        self.logger.info(
            f"Session edit dialog opened: {self.editing_session.name} ({'new' if self.is_new_item else 'edit'})"
        )

    def _on_map(self, widget):
        if self.name_row:
            self.name_row.grab_focus()

    def _setup_ui(self) -> None:
        try:
            # Apply custom CSS for modern styling
            self._apply_custom_css()

            # Use Adw.ToolbarView for proper header bar integration
            toolbar_view = Adw.ToolbarView()
            self.set_content(toolbar_view)

            # Header bar with title
            header = Adw.HeaderBar()
            header.set_show_end_title_buttons(True)
            header.set_show_start_title_buttons(False)

            # Cancel button on left
            cancel_button = Gtk.Button(label=_("Cancel"))
            cancel_button.connect("clicked", self._on_cancel_clicked)
            header.pack_start(cancel_button)

            # Save button on right (first pack_end so it's rightmost)
            save_button = Gtk.Button(
                label=_("Save"), css_classes=[BaseDialog.CSS_CLASS_SUGGESTED]
            )
            save_button.connect("clicked", self._on_save_clicked)
            header.pack_end(save_button)
            self.set_default_widget(save_button)

            # Test connection button (to the left of Save, only for SSH)
            self.test_button = Gtk.Button(label=_("Test Connection"))
            get_tooltip_helper().add_tooltip(self.test_button, _("Test SSH connection"))
            self.test_button.connect("clicked", self._on_test_connection_clicked)
            header.pack_end(self.test_button)

            toolbar_view.add_top_bar(header)

            # Scrolled window with preferences page
            scrolled = Gtk.ScrolledWindow()
            scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
            toolbar_view.set_content(scrolled)

            # Preferences page for proper styling
            prefs_page = Adw.PreferencesPage()
            scrolled.set_child(prefs_page)

            # Create all sections
            self._create_name_section(prefs_page)
            self._create_local_terminal_section(prefs_page)
            self._create_ssh_section(prefs_page)
            self._create_additional_section(prefs_page)

            self._update_ssh_visibility()
            self._update_local_visibility()
            self._update_auth_visibility()
        except Exception as e:
            self.logger.error(f"Failed to setup UI: {e}")
            self._show_error_dialog(
                _("UI Error"), _("Failed to initialize dialog interface")
            )
            self.close()

    def _apply_custom_css(self) -> None:
        """Custom CSS for startup commands section is now loaded globally.

        The styles are defined in data/styles/components.css:
        - .startup-commands-container: Modern rounded container
        - .startup-commands-text: Monospace text view styling

        These styles are loaded at app startup by window_ui.py.
        """
        pass  # CSS is loaded globally from components.css

    def _apply_bash_colors(self, text_view: BashTextView) -> None:
        """Apply terminal color scheme to BashTextView if settings_manager is available."""
        if not self._settings_manager:
            return

        gtk_theme = self._settings_manager.get("gtk_theme", "system")
        if gtk_theme == "terminal":
            scheme_data = self._settings_manager.get_color_scheme_data()
            if scheme_data:
                palette = scheme_data.get("palette", [])
                foreground = scheme_data.get("foreground", "#ffffff")
                if palette:
                    text_view.update_colors_from_scheme(palette, foreground)

    # NOTE: _create_entry_row and _create_spin_row are inherited from BaseDialog

    def _create_name_section(self, parent: Adw.PreferencesPage) -> None:
        """Create the session information section with proper Adw widgets."""
        name_group = Adw.PreferencesGroup()

        # Session Name - using helper
        self.name_row = self._create_entry_row(
            title=_("Session Name"),
            text=self.editing_session.name,
            on_changed=self._on_validated_entry_changed,
        )
        name_group.add(self.name_row)

        # Session Type - using Adw.ComboRow properly
        self.type_combo = Adw.ComboRow(
            title=_("Session Type"),
            subtitle=_("Choose between local terminal or SSH connection"),
        )
        self.type_combo.set_model(
            Gtk.StringList.new([_("Local Terminal"), _("SSH Connection")])
        )
        self.type_combo.set_selected(0 if self.editing_session.is_local() else 1)
        self.type_combo.connect(
            BaseDialog.SIGNAL_NOTIFY_SELECTED, self._on_type_changed
        )
        name_group.add(self.type_combo)

        parent.add(name_group)

    def _create_additional_section(self, parent: Adw.PreferencesPage) -> None:
        """Create the additional settings section: Tab Color, Folder, Highlighting."""
        additional_group = Adw.PreferencesGroup()

        # Tab Color
        color_row = Adw.ActionRow(
            title=_("Tab Color"),
            subtitle=_("Choose a color to identify this session's tab"),
        )

        color_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        color_box.set_valign(Gtk.Align.CENTER)

        self.color_button = Gtk.Button(valign=Gtk.Align.CENTER)
        self.color_button.connect("clicked", self._on_select_color_clicked)
        a11y_label(self.color_button, _("Select tab color"))

        initial_rgba = Gdk.RGBA()
        if self.editing_session.tab_color and initial_rgba.parse(
            self.editing_session.tab_color
        ):
            self._update_color_button_content(initial_rgba)
        else:
            self._update_color_button_content(None)

        clear_button = Gtk.Button(
            icon_name="edit-clear-symbolic",
            valign=Gtk.Align.CENTER,
            tooltip_text=_("Clear Color"),
            css_classes=[BaseDialog.CSS_CLASS_FLAT],
        )
        clear_button.connect("clicked", self._on_clear_color_clicked)

        color_box.append(self.color_button)
        color_box.append(clear_button)
        color_row.add_suffix(color_box)
        additional_group.add(color_row)

        # Folder (ExpanderRow)
        if self.folder_store:
            self._add_folder_expander(additional_group)

        # Highlighting (ExpanderRow)
        self._add_highlighting_expander(additional_group)

        parent.add(additional_group)

    def _add_folder_expander(self, group: Adw.PreferencesGroup) -> None:
        _add_folder_expander_impl(self, group)

    def _add_highlighting_expander(self, group: Adw.PreferencesGroup) -> None:
        _add_highlighting_expander_impl(self, group)

    # ---------------------------------------------------------------------
    # Highlighting overrides
    # ---------------------------------------------------------------------

    # Tri-state ↔ ComboRow helpers — see session_edit_form for the
    # pure implementations. Kept as static methods for the (small)
    # legacy call sites that reference them via self.
    _tri_state_to_selected = staticmethod(tri_state_to_selected)
    _selected_to_tri_state = staticmethod(selected_to_tri_state)

    def _create_tristate_combo_row(
        self,
        *,
        title: str,
        subtitle: str,
        initial_value: Optional[bool],
        on_changed,
    ) -> Adw.ComboRow:
        return _create_tristate_combo_row_impl(
            title=title,
            subtitle=subtitle,
            initial_value=initial_value,
            on_changed=on_changed,
        )

    def _on_highlighting_override_changed(self, *_args) -> None:
        if self._updating_highlighting_ui:
            return

        self._mark_changed()

        # Keep the in-memory editing_session synced so other parts of the dialog
        # (and future logic) can rely on the values.
        try:
            # If the user isn't customizing, treat everything as Automatic.
            if not self.highlighting_customize_switch.get_active():
                self.editing_session.output_highlighting = None
                self.editing_session.command_specific_highlighting = None
                self.editing_session.cat_colorization = None
                self.editing_session.shell_input_highlighting = None
                return

            self.editing_session.output_highlighting = self._selected_to_tri_state(
                self.output_highlighting_row.get_selected()
            )
            self.editing_session.command_specific_highlighting = (
                self._selected_to_tri_state(
                    self.command_specific_highlighting_row.get_selected()
                )
            )
            self.editing_session.cat_colorization = self._selected_to_tri_state(
                self.cat_colorization_row.get_selected()
            )
            self.editing_session.shell_input_highlighting = self._selected_to_tri_state(
                self.shell_input_highlighting_row.get_selected()
            )
        except Exception as exc:
            self.logger.warning(f"Failed to sync highlighting overrides: {exc}")

    def _set_highlighting_overrides_visible(self, visible: bool) -> None:
        for row in (
            self.output_highlighting_row,
            self.command_specific_highlighting_row,
            self.cat_colorization_row,
            self.shell_input_highlighting_row,
        ):
            row.set_visible(visible)
            row.set_sensitive(visible)

    def _on_highlighting_customize_switch_changed(self, *_args) -> None:
        if self._updating_highlighting_ui:
            return

        self._mark_changed()
        active = self.highlighting_customize_switch.get_active()

        self._updating_highlighting_ui = True
        try:
            if not active:
                # Equivalent to "Automatic" for all overrides.
                self.output_highlighting_row.set_selected(0)
                self.command_specific_highlighting_row.set_selected(0)
                self.cat_colorization_row.set_selected(0)
                self.shell_input_highlighting_row.set_selected(0)

            self._set_highlighting_overrides_visible(active)
        finally:
            self._updating_highlighting_ui = False

        # Sync editing_session state.
        self._on_highlighting_override_changed()

    def _create_local_terminal_section(self, parent: Adw.PreferencesPage) -> None:
        _create_local_terminal_section_impl(self, parent)

    def _create_ssh_section(self, parent: Adw.PreferencesPage) -> None:
        _create_ssh_section_impl(self, parent)

    def _create_ssh_options_group(self, ssh_group: Adw.PreferencesGroup) -> None:
        _create_ssh_options_group_impl(self, ssh_group)

    def _create_port_forward_widgets(self, parent_group: Adw.PreferencesGroup) -> None:
        """Create port forwarding widgets inside the SSH Options group."""
        # Port forwarding header row (just a separator with title)
        port_forward_header = Adw.ActionRow(
            title=_("Port Forwarding"),
            subtitle=_("Create SSH tunnels to forward ports"),
        )
        port_forward_header.set_activatable(False)
        parent_group.add(port_forward_header)

        # Add button row
        add_row = Adw.ActionRow(
            title=_("Add Port Forward"),
            subtitle=_("Create a new SSH tunnel"),
        )
        add_row.set_activatable(True)
        add_button = Gtk.Button(icon_name="list-add-symbolic")
        add_button.set_valign(Gtk.Align.CENTER)
        add_button.add_css_class(BaseDialog.CSS_CLASS_FLAT)
        a11y_label(add_button, _("Add port forward"))
        add_row.add_suffix(add_button)
        add_row.set_activatable_widget(add_button)
        add_button.connect("clicked", self._on_add_port_forward_clicked)
        parent_group.add(add_row)
        self.port_forward_add_button = add_button
        self.port_forward_add_row = add_row

        # List container for port forwards
        self.port_forward_list = Gtk.ListBox()
        self.port_forward_list.set_selection_mode(Gtk.SelectionMode.NONE)
        self.port_forward_list.add_css_class(BaseDialog.CSS_CLASS_BOXED_LIST)
        a11y_label(self.port_forward_list, _("Port forwarding rules"))

        list_row = Adw.ActionRow()
        list_row.set_child(self.port_forward_list)
        parent_group.add(list_row)
        self.port_forward_list_row = list_row

        # Keep reference for visibility control (use parent_group as proxy)
        self.port_forward_group = parent_group

        self._refresh_port_forward_list()

    def _create_port_forward_widgets_expander(
        self, parent_expander: Adw.ExpanderRow
    ) -> None:
        """Create port forwarding widgets inside an ExpanderRow."""
        port_forward_header = Adw.ActionRow(
            title=_("Port Forwarding"),
            subtitle=_("Create SSH tunnels to forward ports"),
        )
        port_forward_header.set_activatable(False)
        parent_expander.add_row(port_forward_header)

        add_row = Adw.ActionRow(
            title=_("Add Port Forward"),
            subtitle=_("Create a new SSH tunnel"),
        )
        add_row.set_activatable(True)
        add_button = Gtk.Button(icon_name="list-add-symbolic")
        add_button.set_valign(Gtk.Align.CENTER)
        add_button.add_css_class(BaseDialog.CSS_CLASS_FLAT)
        a11y_label(add_button, _("Add port forward"))
        add_row.add_suffix(add_button)
        add_row.set_activatable_widget(add_button)
        add_button.connect("clicked", self._on_add_port_forward_clicked)
        parent_expander.add_row(add_row)
        self.port_forward_add_button = add_button
        self.port_forward_add_row = add_row

        self.port_forward_list = Gtk.ListBox()
        self.port_forward_list.set_selection_mode(Gtk.SelectionMode.NONE)
        self.port_forward_list.add_css_class(BaseDialog.CSS_CLASS_BOXED_LIST)
        a11y_label(self.port_forward_list, _("Port forwarding rules"))

        list_row = Adw.ActionRow()
        list_row.set_child(self.port_forward_list)
        parent_expander.add_row(list_row)
        self.port_forward_list_row = list_row

        self.port_forward_group = None
        self._refresh_port_forward_list()

    def _create_port_forward_section(self, parent: Adw.PreferencesPage) -> None:
        """Legacy method - port forwarding is now inside SSH Options group."""
        pass  # No longer used

    def _refresh_port_forward_list(self) -> None:
        from .session_edit_port_forward import refresh_port_forward_list as _refresh

        _refresh(
            self.port_forward_list,
            self.port_forwardings,
            on_edit=self._on_edit_port_forward_clicked,
            on_delete=self._on_delete_port_forward_clicked,
        )

    def _on_add_port_forward_clicked(self, _button) -> None:
        new_entry = self._show_port_forward_dialog()
        if new_entry:
            self.port_forwardings.append(new_entry)
            self._refresh_port_forward_list()
            self._mark_changed()

    def _on_edit_port_forward_clicked(self, _button, index: int) -> None:
        if 0 <= index < len(self.port_forwardings):
            existing = copy.deepcopy(self.port_forwardings[index])
            updated = self._show_port_forward_dialog(existing)
            if updated:
                self.port_forwardings[index] = updated
                self._refresh_port_forward_list()
                self._mark_changed()

    def _on_delete_port_forward_clicked(self, _button, index: int) -> None:
        if 0 <= index < len(self.port_forwardings):
            del self.port_forwardings[index]
            self._refresh_port_forward_list()
            self._mark_changed()

    def _show_port_forward_dialog(
        self, existing: Optional[dict] = None
    ) -> Optional[dict]:
        """Delegate to extracted port forward dialog module."""
        from .session_edit_port_forward import show_port_forward_dialog as _pf_dialog

        result = _pf_dialog(self, existing)
        if result:
            self._mark_changed()
        return result

    def _on_validated_entry_changed(self, entry) -> None:
        entry.remove_css_class(self.CSS_CLASS_ERROR)
        self._mark_changed()

    def _update_color_button_content(self, rgba: Optional[Gdk.RGBA]) -> None:
        """Update the button content to show the selected color swatch."""
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)

        # Color swatch
        swatch = Gtk.DrawingArea()
        swatch.set_content_width(24)
        swatch.set_content_height(16)
        a11y_label(swatch, _("Tab color preview"))

        if rgba:

            def draw_func(_area, cr, _width, _height, color=rgba):
                Gdk.cairo_set_source_rgba(cr, color)
                cr.paint()

            swatch.set_draw_func(draw_func)
            label_text = rgba.to_string()
        else:
            # Draw a frame or 'X' for no color
            def draw_empty(_area, cr, width, height):
                cr.set_source_rgb(0.5, 0.5, 0.5)
                cr.rectangle(0, 0, width, height)
                cr.stroke()

            swatch.set_draw_func(draw_empty)
            label_text = _("None")

        box.append(swatch)
        box.append(Gtk.Label(label=label_text))
        self.color_button.set_child(box)

    def _on_select_color_clicked(self, _button) -> None:
        """Open async color dialog."""
        dialog = Gtk.ColorDialog(title=_("Select Tab Color"))

        # Get start color
        current_rgba = Gdk.RGBA()
        if self.editing_session.tab_color:
            current_rgba.parse(self.editing_session.tab_color)

        dialog.choose_rgba(self, current_rgba, None, self._on_color_chosen)

    def _on_color_chosen(self, source, result) -> None:
        """Handle async color selection."""
        try:
            rgba = source.choose_rgba_finish(result)
            if rgba:
                self.editing_session.tab_color = rgba.to_string()
                self._update_color_button_content(rgba)
                self._mark_changed()
        except GLib.Error:
            # Cancelled
            pass
        except Exception as e:
            self.logger.error(f"Color selection failed: {e}")

    def _on_clear_color_clicked(self, _button):
        self.editing_session.tab_color = None
        self._update_color_button_content(None)
        self._mark_changed()

    def _on_folder_changed(self, combo_row, param) -> None:
        self._mark_changed()

    def _on_type_changed(self, combo_row, param) -> None:
        self._update_ssh_visibility()
        self._update_local_visibility()
        self._mark_changed()

    def _on_host_changed(self, entry: Gtk.Entry) -> None:
        entry.remove_css_class(self.CSS_CLASS_ERROR)
        self._clear_field_error(entry)
        self._mark_changed()
        hostname = entry.get_text().strip()
        if hostname and not HostnameValidator.is_valid_hostname(hostname):
            entry.add_css_class(self.CSS_CLASS_ERROR)
            self._set_field_error(entry, _("Invalid hostname format"))

    def _on_user_changed(self, entry: Gtk.Entry) -> None:
        self._mark_changed()

    def _on_port_changed(self, value: float) -> None:
        self._mark_changed()
        port = int(value)
        if 1 <= port <= 65535:
            self.port_row.remove_css_class(self.CSS_CLASS_ERROR)
            self._clear_field_error(self.port_row)
        else:
            self.port_row.add_css_class(self.CSS_CLASS_ERROR)
            self._set_field_error(self.port_row, _("Port must be between 1 and 65535"))

    def _on_auth_changed(self, combo_row, param) -> None:
        self._update_auth_visibility()
        self._mark_changed()

    def _on_key_path_changed(self, entry: Gtk.Entry) -> None:
        entry.remove_css_class(self.CSS_CLASS_ERROR)
        self._clear_field_error(entry)
        self._mark_changed()
        key_path = entry.get_text().strip()
        if key_path:
            try:
                validate_ssh_key_file(key_path)
            except Exception as e:
                entry.add_css_class(self.CSS_CLASS_ERROR)
                self._set_field_error(entry, str(e))

    def _on_password_changed(self, entry: Gtk.PasswordEntry) -> None:
        self._mark_changed()

    def _on_post_login_toggle(self, switch_row: Adw.SwitchRow, _param) -> None:
        self._mark_changed()
        # Show/hide the command container based on switch state
        is_enabled = switch_row.get_active()
        if self.post_login_command_container:
            self.post_login_command_container.set_visible(is_enabled)
        self._update_post_login_command_state()
        if self.post_login_entry and not is_enabled:
            self.post_login_entry.remove_css_class(self.CSS_CLASS_ERROR)

    def _on_post_login_command_changed(self, buffer: Gtk.TextBuffer) -> None:
        if self.post_login_entry:
            self.post_login_entry.remove_css_class(self.CSS_CLASS_ERROR)
        self._mark_changed()

    def _on_x11_toggled(self, switch_row: Adw.SwitchRow, _param) -> None:
        if switch_row.get_active() and hasattr(self.parent_window, "toast_overlay"):
            toast = Adw.Toast(
                title=_("Remote server must have X11Forwarding yes in sshd_config.")
            )
            self.parent_window.toast_overlay.add_toast(toast)
        self._mark_changed()

    def _on_sftp_toggle(self, switch_row: Adw.SwitchRow, _param) -> None:
        self._mark_changed()
        self._update_sftp_state()
        if self.sftp_local_entry and not switch_row.get_active():
            self.sftp_local_entry.remove_css_class(self.CSS_CLASS_ERROR)

    def _on_sftp_remote_changed(self, entry: Gtk.Entry) -> None:
        self._mark_changed()

    # ── Visibility delegators ───────────────────────────────
    # State-machine for "what's visible/sensitive right now" lives
    # in session_edit_visibility.

    def _update_ssh_visibility(self) -> None:
        _update_ssh_visibility_impl(self)

    def _update_local_visibility(self) -> None:
        _update_local_visibility_impl(self)

    def _update_auth_visibility(self) -> None:
        _update_auth_visibility_impl(self)

    def _update_port_forward_state(self) -> None:
        _update_port_forward_state_impl(self)

    def _update_post_login_command_state(self) -> None:
        _update_post_login_command_state_impl(self)

    def _update_sftp_state(self) -> None:
        _update_sftp_state_impl(self)

    def _on_local_working_dir_changed(self, entry: Gtk.Entry) -> None:
        """Handle changes to the local working directory."""
        self._mark_changed()
        path_text = entry.get_text().strip()
        if path_text:
            try:
                path = Path(path_text).expanduser()
                if not path.exists() or not path.is_dir():
                    entry.add_css_class(self.CSS_CLASS_ERROR)
                    self._set_field_error(entry, _("Directory does not exist"))
                else:
                    entry.remove_css_class(self.CSS_CLASS_ERROR)
                    self._clear_field_error(entry)
            except Exception:
                entry.add_css_class(self.CSS_CLASS_ERROR)
                self._set_field_error(entry, _("Invalid path"))
        else:
            entry.remove_css_class(self.CSS_CLASS_ERROR)
            self._clear_field_error(entry)

    def _on_local_startup_command_changed(self, buffer: Gtk.TextBuffer) -> None:
        """Handle changes to the local startup commands."""
        self._mark_changed()

    def _on_browse_working_dir_clicked(self, button) -> None:
        """Browse for a working directory folder."""
        try:
            file_dialog = Gtk.FileDialog(title=_("Select Start Folder"), modal=True)
            home_dir = Path.home()
            current_path = self.local_working_dir_entry.get_text().strip()
            if current_path:
                try:
                    path = Path(current_path).expanduser()
                    if path.exists() and path.is_dir():
                        file_dialog.set_initial_folder(Gio.File.new_for_path(str(path)))
                    else:
                        file_dialog.set_initial_folder(
                            Gio.File.new_for_path(str(home_dir))
                        )
                except Exception:
                    file_dialog.set_initial_folder(Gio.File.new_for_path(str(home_dir)))
            else:
                file_dialog.set_initial_folder(Gio.File.new_for_path(str(home_dir)))
            file_dialog.select_folder(self, None, self._on_working_dir_dialog_response)
        except Exception as e:
            self.logger.error(f"Browse working dir dialog failed: {e}")
            self._show_error_dialog(
                _("File Dialog Error"), _("Failed to open folder browser")
            )

    def _on_working_dir_dialog_response(self, dialog, result) -> None:
        """Handle the working directory folder selection response."""
        try:
            folder = dialog.select_folder_finish(result)
            if folder and (path := folder.get_path()):
                self.local_working_dir_entry.set_text(path)
                self.local_working_dir_entry.remove_css_class(self.CSS_CLASS_ERROR)
        except GLib.Error as e:
            if not e.matches(Gio.io_error_quark(), Gio.IOErrorEnum.CANCELLED):
                self.logger.error(f"Folder dialog error: {e.message}")
                self._show_error_dialog(
                    _("Folder Selection Error"),
                    _("Failed to select folder: {}").format(e.message),
                )
        except Exception as e:
            self.logger.error(f"Folder dialog response handling failed: {e}")

    def _on_browse_key_clicked(self, button) -> None:
        try:
            file_dialog = Gtk.FileDialog(title=_("Select SSH Key"), modal=True)
            ssh_dir = get_ssh_directory()
            if ssh_dir.exists():
                file_dialog.set_initial_folder(Gio.File.new_for_path(str(ssh_dir)))
            file_dialog.open(self, None, self._on_file_dialog_response)
        except Exception as e:
            self.logger.error(f"Browse key dialog failed: {e}")
            self._show_error_dialog(
                _("File Dialog Error"), _("Failed to open file browser")
            )

    def _on_file_dialog_response(self, dialog, result) -> None:
        try:
            file = dialog.open_finish(result)
            if file and (path := file.get_path()):
                try:
                    validate_ssh_key_file(path)
                    self.key_path_entry.set_text(path)
                    self.key_path_entry.remove_css_class(BaseDialog.CSS_CLASS_ERROR)
                except SSHKeyError as e:
                    self._show_error_dialog(_("Invalid SSH Key"), e.user_message)
        except GLib.Error as e:
            if not e.matches(Gio.io_error_quark(), Gio.IOErrorEnum.CANCELLED):
                self.logger.error(f"File dialog error: {e.message}")
                self._show_error_dialog(
                    _("File Selection Error"),
                    _("Failed to select file: {}").format(e.message),
                )
        except Exception as e:
            self.logger.error(f"File dialog response handling failed: {e}")

    def _on_test_connection_clicked(self, button) -> None:
        try:
            test_session = self._create_session_from_fields()
            if not test_session:
                self._show_error_dialog(
                    BaseDialog.MSG_VALIDATION_ERROR,
                    _("Please fill in all required SSH fields first."),
                )
                return
            self.testing_dialog = Adw.AlertDialog(
                heading=_("Testing Connection..."),
                body=_("Attempting to connect to {host}...").format(
                    host=test_session.host
                ),
            )
            spinner = Gtk.Spinner(spinning=True, halign=Gtk.Align.CENTER, margin_top=12)
            self.testing_dialog.set_extra_child(spinner)
            self.testing_dialog.present(self)
            from ...core.tasks import AsyncTaskManager

            AsyncTaskManager.get().submit_io(self._run_test_in_thread, test_session)
        except Exception as e:
            self.logger.error(f"Test connection setup failed: {e}")
            if self.testing_dialog:
                self.testing_dialog.close()
            self._show_error_dialog(
                _("Test Connection Error"),
                _("Failed to start connection test: {}").format(e),
            )

    def _create_session_from_fields(self) -> Optional[SessionItem]:
        if (
            not self.host_entry.get_text().strip()
            or not self.user_entry.get_text().strip()
        ):
            return None
        is_password_auth = self.auth_combo.get_selected() != 0
        session = SessionItem(
            name="Test Connection",
            session_type="ssh",
            host=self.host_entry.get_text().strip(),
            user=self.user_entry.get_text().strip(),
            port=int(self.port_entry.get_value()),
            auth_type="password" if is_password_auth else "key",
            auth_value="" if is_password_auth else self._get_auth_value(),
        )
        # For password auth, bypass keyring by setting internal field directly
        # This allows test_ssh_connection to access the password without saving
        if is_password_auth:
            session._auth_value = self.password_entry.get_text()
        return session

    def _run_test_in_thread(self, test_session: SessionItem):
        # Lazy import to defer loading until actually testing connection
        from ...terminal.spawner import get_spawner

        spawner = get_spawner()
        success, message = spawner.test_ssh_connection(test_session)
        GLib.idle_add(self._on_test_finished, success, message)

    def _on_test_finished(self, success: bool, message: str):
        if self.testing_dialog:
            self.testing_dialog.close()
        if success:
            result_dialog = Adw.AlertDialog(
                heading=_("Connection Successful"),
                body=_("Successfully connected to the SSH server."),
            )
            result_dialog.add_response("ok", _("OK"))
            result_dialog.present(self)
        else:
            self._show_error_dialog(
                _("Connection Failed"),
                _("Could not connect to the SSH server."),
                details=message,
            )
        return False

    def _on_cancel_clicked(self, button) -> None:
        try:
            if self._has_changes:
                self._show_warning_dialog(
                    _("Unsaved Changes"),
                    _("You have unsaved changes. Are you sure you want to cancel?"),
                    lambda: self.close(),
                )
            else:
                self.close()
        except Exception as e:
            self.logger.error(f"Cancel handling failed: {e}")
            self.close()

    def _on_save_clicked(self, button) -> None:
        """Handles the save button click by delegating to the SessionOperations layer."""
        try:
            updated_session = self._build_updated_session()
            if not updated_session:
                return  # Validation failed and showed a dialog

            operations = self.parent_window.session_operations
            if self.is_new_item:
                result = operations.add_session(updated_session)
            else:
                result = operations.update_session(self.position, updated_session)

            if result and result.success:
                self.logger.info(
                    f"Session operation successful: {updated_session.name}"
                )
                # Tree refresh is handled automatically via AppSignals
                self.close()
            elif result:
                self._show_error_dialog(_("Save Error"), result.message)
        except Exception as e:
            self.logger.error(f"Save handling failed: {e}")
            self._show_error_dialog(
                _("Save Error"), _("An unexpected error occurred while saving.")
            )

    def _build_updated_session(self) -> Optional[SessionItem]:
        """Builds a SessionItem from dialog fields and performs validation."""
        self._clear_validation_errors()
        if not self._validate_basic_fields():
            return None

        is_local = self.type_combo.get_selected() == 0
        if is_local and not self._validate_local_fields():
            return None
        if not is_local and not self._validate_ssh_fields():
            return None

        updated_session = self.form_collector.build_session(is_local)

        if not updated_session.validate():
            errors = updated_session.get_validation_errors()
            self._show_error_dialog(
                _("Validation Error"),
                _("Session validation failed:\n{}").format("\n".join(errors)),
            )
            return None

        return updated_session

    # The data-collection pipeline lives in ``SessionFormCollector``.
    # These delegators keep existing references working (tests, subclass
    # overrides) without bloating the dialog.
    def _collect_session_data(self, is_local: bool) -> dict:
        return self.form_collector.collect_data(is_local)

    def _get_raw_password(self, session_data: dict) -> str:
        return self.form_collector.get_raw_password(session_data)

    # ── Validator delegators ────────────────────────────────
    # Pure predicates live in session_edit_validators. Keeping the
    # ``self._validate_*`` naming avoids churn in call sites.

    def _validate_basic_fields(self) -> bool:
        return validate_basic(self)

    def _validate_local_fields(self) -> bool:
        return validate_local(self)

    def _validate_hostname_field(self) -> bool:
        return validate_hostname(self)

    def _validate_ssh_key_field(self) -> bool:
        return validate_ssh_key(self)

    def _validate_post_login_field(self) -> bool:
        return validate_post_login(self)

    def _validate_sftp_directory_field(self) -> bool:
        return validate_sftp_directory(self)

    def _validate_ssh_fields(self) -> bool:
        return validate_ssh_bundle(self)

    def _validate_port_forward_data(self, data: dict) -> list[str]:
        return validate_port_forward(data)

    def _get_auth_value(self) -> str:
        if self.type_combo.get_selected() == 0:
            return ""
        elif self.auth_combo.get_selected() == 0:
            return self.key_path_entry.get_text().strip()
        else:
            return self.password_entry.get_text()
