# ashyterm/ui/dialogs/session_edit_dialog.py

import copy
import threading
from pathlib import Path
from typing import Optional

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gio, GLib, Gtk

from ...sessions.models import SessionItem
from ...utils.exceptions import HostnameValidationError, SSHKeyError
from ...utils.platform import get_ssh_directory
from ...utils.security import (
    HostnameValidator,
    validate_ssh_hostname,
    validate_ssh_key_file,
)
from ...utils.tooltip_helper import get_tooltip_helper
from ...utils.translation_utils import _
from .base_dialog import BaseDialog


class SessionEditDialog(BaseDialog):
    def __init__(
        self,
        parent_window,
        session_item: SessionItem,
        session_store,
        position: int,
        folder_store=None,
    ):
        self.is_new_item = position == -1
        title = _("Add Session") if self.is_new_item else _("Edit Session")
        super().__init__(parent_window, title, default_width=700, default_height=720)

        self.session_store = session_store
        self.folder_store = folder_store
        self.position = position
        # The editing_session is a data holder, not the live store object
        self.editing_session = (
            SessionItem.from_dict(session_item.to_dict())
            if not self.is_new_item
            else session_item
        )
        self.original_session = session_item if not self.is_new_item else None
        self.folder_paths_map: dict[str, str] = {}
        self.post_login_switch: Optional[Adw.SwitchRow] = None
        self.post_login_entry: Optional[Adw.EntryRow] = None
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
        # Local terminal options
        self.local_terminal_group: Optional[Adw.PreferencesGroup] = None
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
            save_button = Gtk.Button(label=_("Save"), css_classes=["suggested-action"])
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
            if self.folder_store:
                self._create_folder_section(prefs_page)
            self._create_local_terminal_section(prefs_page)
            self._create_ssh_section(prefs_page)

            self._update_ssh_visibility()
            self._update_local_visibility()
            self._update_auth_visibility()
        except Exception as e:
            self.logger.error(f"Failed to setup UI: {e}")
            self._show_error_dialog(
                _("UI Error"), _("Failed to initialize dialog interface")
            )
            self.close()

    def _create_name_section(self, parent: Adw.PreferencesPage) -> None:
        """Create the session information section with proper Adw widgets."""
        name_group = Adw.PreferencesGroup()

        # Session Name - using Adw.EntryRow
        self.name_row = Adw.EntryRow(
            title=_("Session Name"),
        )
        self.name_row.set_text(self.editing_session.name)
        self.name_row.connect("changed", self._on_name_changed)
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
        self.type_combo.connect("notify::selected", self._on_type_changed)
        name_group.add(self.type_combo)

        # Tab Color - using Adw.ActionRow with color button
        color_row = Adw.ActionRow(
            title=_("Tab Color"),
            subtitle=_("Choose a color to identify this session's tab"),
        )

        color_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        color_box.set_valign(Gtk.Align.CENTER)

        self.color_button = Gtk.ColorButton(valign=Gtk.Align.CENTER, show_editor=True)
        self.color_button.connect("color-set", self._on_color_changed)

        if self.editing_session.tab_color:
            rgba = Gdk.RGBA()
            if rgba.parse(self.editing_session.tab_color):
                self.color_button.set_rgba(rgba)
            else:
                self.color_button.set_rgba(Gdk.RGBA())
        else:
            self.color_button.set_rgba(Gdk.RGBA())

        clear_button = Gtk.Button(
            icon_name="edit-clear-symbolic",
            valign=Gtk.Align.CENTER,
            tooltip_text=_("Clear Color"),
            css_classes=["flat"],
        )
        clear_button.connect("clicked", self._on_clear_color_clicked)

        color_box.append(self.color_button)
        color_box.append(clear_button)
        color_row.add_suffix(color_box)
        name_group.add(color_row)

        parent.add(name_group)

    def _create_folder_section(self, parent: Adw.PreferencesPage) -> None:
        """Create the folder organization section."""
        folder_group = Adw.PreferencesGroup(
            title=_("Organization"),
            description=_("Choose where to store this session"),
        )

        folder_row = Adw.ComboRow(
            title=_("Folder"),
            subtitle=_("Select a folder to organize this session"),
        )

        folder_model = Gtk.StringList()
        folder_model.append(_("Root"))
        self.folder_paths_map = {_("Root"): ""}

        folders = sorted(
            [
                self.folder_store.get_item(i)
                for i in range(self.folder_store.get_n_items())
            ],
            key=lambda f: f.path,
        )
        for folder in folders:
            display_name = f"{'  ' * folder.path.count('/')}{folder.name}"
            folder_model.append(display_name)
            self.folder_paths_map[display_name] = folder.path

        folder_row.set_model(folder_model)

        selected_index = 0
        for i, (display, path_val) in enumerate(self.folder_paths_map.items()):
            if path_val == self.editing_session.folder_path:
                selected_index = i
                break
        folder_row.set_selected(selected_index)
        folder_row.connect("notify::selected", self._on_folder_changed)
        self.folder_combo = folder_row

        folder_group.add(folder_row)
        parent.add(folder_group)

    def _create_local_terminal_section(self, parent: Adw.PreferencesPage) -> None:
        """Create the Local Terminal configuration section."""
        local_group = Adw.PreferencesGroup(
            title=_("Local Terminal Options"),
        )

        # Working Directory - using Adw.ActionRow with entry and browse button
        working_dir_row = Adw.ActionRow(
            title=_("Working Directory"),
            subtitle=_("Start the terminal in this folder"),
        )

        working_dir_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        working_dir_box.set_valign(Gtk.Align.CENTER)
        working_dir_box.set_hexpand(True)

        self.local_working_dir_entry = Gtk.Entry(
            text=self.editing_session.local_working_directory or "",
            placeholder_text=_("Default (Home directory)"),
            hexpand=True,
            width_chars=25,
        )
        self.local_working_dir_entry.connect(
            "changed", self._on_local_working_dir_changed
        )

        browse_working_dir_button = Gtk.Button(
            icon_name="folder-open-symbolic",
            tooltip_text=_("Browse for folder"),
            css_classes=["flat"],
        )
        browse_working_dir_button.set_valign(Gtk.Align.CENTER)
        browse_working_dir_button.connect(
            "clicked", self._on_browse_working_dir_clicked
        )

        working_dir_box.append(self.local_working_dir_entry)
        working_dir_box.append(browse_working_dir_button)
        working_dir_row.add_suffix(working_dir_box)
        local_group.add(working_dir_row)

        # Startup Commands - using multi-line TextView for script-like input
        # Create a container box for the title and help text
        commands_header_box = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=4,
            margin_top=12,
            margin_bottom=4,
            margin_start=12,
        )
        commands_title = Gtk.Label(
            label=_("Startup Commands"),
            xalign=0,
            css_classes=["title-4"],
        )
        commands_header_box.append(commands_title)

        commands_subtitle = Gtk.Label(
            label=_(
                "Commands executed when the terminal starts (one per line). You can write multiple commands like a small script."
            ),
            xalign=0,
            wrap=True,
            css_classes=["dim-label"],
        )
        commands_header_box.append(commands_subtitle)
        local_group.add(commands_header_box)

        # Create a scrolled window with TextView for multi-line input
        scrolled = Gtk.ScrolledWindow()
        scrolled.set_min_content_height(100)
        scrolled.set_max_content_height(180)
        scrolled.set_margin_start(12)
        scrolled.set_margin_end(12)
        scrolled.set_margin_bottom(8)
        scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scrolled.add_css_class("card")

        self.local_startup_command_view = Gtk.TextView()
        self.local_startup_command_view.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        self.local_startup_command_view.set_pixels_above_lines(6)
        self.local_startup_command_view.set_pixels_below_lines(6)
        self.local_startup_command_view.set_left_margin(10)
        self.local_startup_command_view.set_right_margin(10)
        self.local_startup_command_view.add_css_class("monospace")

        # Set initial text
        buffer = self.local_startup_command_view.get_buffer()
        buffer.set_text(self.editing_session.local_startup_command or "")
        buffer.connect("changed", self._on_local_startup_command_changed)

        scrolled.set_child(self.local_startup_command_view)
        local_group.add(scrolled)

        self.local_terminal_group = local_group
        parent.add(local_group)

    def _create_ssh_section(self, parent: Adw.PreferencesPage) -> None:
        """Create the SSH configuration section with proper Adw widgets."""
        ssh_group = Adw.PreferencesGroup(
            title=_("SSH Configuration"),
        )

        # Host - using Adw.EntryRow
        self.host_row = Adw.EntryRow(
            title=_("Host"),
        )
        self.host_row.set_text(self.editing_session.host or "")
        self.host_row.connect("changed", self._on_host_changed)
        ssh_group.add(self.host_row)
        # Keep reference with old name for compatibility
        self.host_entry = self.host_row

        # Username - using Adw.EntryRow
        self.user_row = Adw.EntryRow(
            title=_("Username"),
        )
        self.user_row.set_text(self.editing_session.user or "")
        self.user_row.connect("changed", self._on_user_changed)
        ssh_group.add(self.user_row)
        # Keep reference with old name for compatibility
        self.user_entry = self.user_row

        # Port - using Adw.SpinRow
        self.port_row = Adw.SpinRow.new_with_range(1, 65535, 1)
        self.port_row.set_title(_("Port"))
        self.port_row.set_value(self.editing_session.port or 22)
        self.port_row.connect("notify::value", self._on_port_changed)
        ssh_group.add(self.port_row)
        # Keep reference with old name for compatibility
        self.port_entry = self.port_row

        # Authentication Method - using Adw.ComboRow
        self.auth_combo = Adw.ComboRow(
            title=_("Authentication"),
            subtitle=_("Choose how to authenticate with the server"),
        )
        self.auth_combo.set_model(Gtk.StringList.new([_("SSH Key"), _("Password")]))
        self.auth_combo.set_selected(0 if self.editing_session.uses_key_auth() else 1)
        self.auth_combo.connect("notify::selected", self._on_auth_changed)
        ssh_group.add(self.auth_combo)

        # SSH Key Path - using Adw.ActionRow with entry and browse button
        key_value = (
            self.editing_session.auth_value
            if self.editing_session.uses_key_auth()
            else ""
        )
        self.key_row = Adw.ActionRow(
            title=_("SSH Key Path"),
            subtitle=_("Path to your private key file"),
        )

        key_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        key_box.set_valign(Gtk.Align.CENTER)
        key_box.set_hexpand(True)

        self.key_path_entry = Gtk.Entry(
            text=key_value,
            placeholder_text=f"{get_ssh_directory()}/id_rsa",
            hexpand=True,
            width_chars=30,
        )
        self.key_path_entry.connect("changed", self._on_key_path_changed)

        self.browse_button = Gtk.Button(
            icon_name="folder-open-symbolic",
            tooltip_text=_("Browse for SSH key file"),
            css_classes=["flat"],
        )
        self.browse_button.set_valign(Gtk.Align.CENTER)
        self.browse_button.connect("clicked", self._on_browse_key_clicked)

        key_box.append(self.key_path_entry)
        key_box.append(self.browse_button)
        self.key_row.add_suffix(key_box)
        ssh_group.add(self.key_row)
        self.key_box = self.key_row  # Keep reference for visibility control

        # Password - using Adw.PasswordEntryRow
        password_value = (
            self.editing_session.auth_value
            if self.editing_session.uses_password_auth()
            else ""
        )
        self.password_row = Adw.PasswordEntryRow(
            title=_("Password"),
        )
        self.password_row.set_text(password_value)
        self.password_row.connect("changed", self._on_password_changed)
        ssh_group.add(self.password_row)
        # Keep reference with old name for compatibility
        self.password_entry = self.password_row
        self.password_box = self.password_row  # Keep reference for visibility control

        # Add keyring info
        from ...utils.crypto import is_encryption_available

        if not is_encryption_available():
            keyring_row = Adw.ActionRow(
                title=_("Password Storage"),
                subtitle=_(
                    "System keyring not available - password will be stored in plain text"
                ),
            )
            keyring_row.add_prefix(
                Gtk.Image.new_from_icon_name("dialog-warning-symbolic")
            )
            ssh_group.add(keyring_row)

        self.ssh_box = ssh_group
        parent.add(ssh_group)

        # Create additional SSH options in a separate group
        self._create_ssh_options_group(parent)

    def _create_ssh_options_group(self, parent: Adw.PreferencesPage) -> None:
        """Create additional SSH options like post-login command, X11, SFTP."""
        options_group = Adw.PreferencesGroup(
            title=_("SSH Options"),
        )

        # Post-login command toggle
        self.post_login_switch = Adw.SwitchRow(
            title=_("Run Command After Login"),
            subtitle=_("Execute a command automatically after SSH connects"),
        )
        self.post_login_switch.set_active(
            self.editing_session.post_login_command_enabled
        )
        self.post_login_switch.connect("notify::active", self._on_post_login_toggle)
        options_group.add(self.post_login_switch)

        # Post-login command entry
        self.post_login_entry = Adw.EntryRow(
            title=_("Post-Login Command"),
        )
        self.post_login_entry.set_text(self.editing_session.post_login_command or "")
        self.post_login_entry.connect("changed", self._on_post_login_command_changed)
        options_group.add(self.post_login_entry)
        self.post_login_command_row = self.post_login_entry

        # X11 Forwarding toggle
        self.x11_switch = Adw.SwitchRow(
            title=_("Enable X11 Forwarding"),
            subtitle=_("Allow graphical applications from remote server"),
        )
        self.x11_switch.set_active(self.editing_session.x11_forwarding)
        self.x11_switch.connect("notify::active", self._on_x11_toggled)
        options_group.add(self.x11_switch)

        # SFTP toggle
        self.sftp_switch = Adw.SwitchRow(
            title=_("Enable SFTP Session"),
            subtitle=_("Use default directories when opening SFTP"),
        )
        self.sftp_switch.set_active(self.editing_session.sftp_session_enabled)
        self.sftp_switch.connect("notify::active", self._on_sftp_toggle)
        options_group.add(self.sftp_switch)

        # SFTP Local Directory
        self.sftp_local_entry = Adw.EntryRow(
            title=_("SFTP Local Directory"),
        )
        self.sftp_local_entry.set_text(self.editing_session.sftp_local_directory or "")
        self.sftp_local_entry.connect("changed", self._on_sftp_local_changed)
        options_group.add(self.sftp_local_entry)
        self.sftp_local_row = self.sftp_local_entry

        # SFTP Remote Directory
        self.sftp_remote_entry = Adw.EntryRow(
            title=_("SFTP Remote Directory"),
        )
        self.sftp_remote_entry.set_text(
            self.editing_session.sftp_remote_directory or ""
        )
        self.sftp_remote_entry.connect("changed", self._on_sftp_remote_changed)
        options_group.add(self.sftp_remote_entry)
        self.sftp_remote_row = self.sftp_remote_entry

        # Port Forwarding section (inside SSH Options)
        self._create_port_forward_widgets(options_group)

        self.ssh_options_group = options_group
        parent.add(options_group)

        # Update initial visibility
        self._update_post_login_command_state()
        self._update_sftp_state()

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
        add_button.add_css_class("flat")
        add_row.add_suffix(add_button)
        add_row.set_activatable_widget(add_button)
        add_button.connect("clicked", self._on_add_port_forward_clicked)
        parent_group.add(add_row)
        self.port_forward_add_button = add_button
        self.port_forward_add_row = add_row

        # List container for port forwards
        self.port_forward_list = Gtk.ListBox()
        self.port_forward_list.set_selection_mode(Gtk.SelectionMode.NONE)
        self.port_forward_list.add_css_class("boxed-list")

        list_row = Adw.ActionRow()
        list_row.set_child(self.port_forward_list)
        parent_group.add(list_row)
        self.port_forward_list_row = list_row

        # Keep reference for visibility control (use parent_group as proxy)
        self.port_forward_group = parent_group

        self._refresh_port_forward_list()

    def _create_port_forward_section(self, parent: Adw.PreferencesPage) -> None:
        """Legacy method - port forwarding is now inside SSH Options group."""
        pass  # No longer used

    def _refresh_port_forward_list(self) -> None:
        if not self.port_forward_list:
            return
        # Gtk4 ListBox does not expose get_children; iterate manually.
        child = self.port_forward_list.get_first_child()
        while child:
            next_child = child.get_next_sibling()
            self.port_forward_list.remove(child)
            child = next_child

        if not self.port_forwardings:
            placeholder_row = Gtk.ListBoxRow()
            placeholder_row.set_selectable(False)
            placeholder_row.set_activatable(False)
            label = Gtk.Label(
                label=_("No port forwards configured."),
                xalign=0,
                margin_top=6,
                margin_bottom=6,
                margin_start=12,
                margin_end=12,
            )
            label.add_css_class("dim-label")
            placeholder_row.set_child(label)
            self.port_forward_list.append(placeholder_row)
            return

        for index, tunnel in enumerate(self.port_forwardings):
            row = Gtk.ListBoxRow()
            row.set_selectable(False)
            row.set_activatable(False)
            row_box = Gtk.Box(
                orientation=Gtk.Orientation.HORIZONTAL,
                spacing=12,
                margin_top=6,
                margin_bottom=6,
                margin_start=12,
                margin_end=12,
            )

            labels_box = Gtk.Box(
                orientation=Gtk.Orientation.VERTICAL,
                spacing=2,
                hexpand=True,
            )
            title = Gtk.Label(
                label=tunnel.get("name", _("Tunnel")),
                xalign=0,
            )
            remote_host_display = tunnel.get("remote_host") or _("SSH Host")
            subtitle_text = _("{local_host}:{local_port} → {remote_host}:{remote_port}").format(
                local_host=tunnel.get("local_host", "localhost"),
                local_port=tunnel.get("local_port", 0),
                remote_host=remote_host_display,
                remote_port=tunnel.get("remote_port", 0),
            )
            subtitle = Gtk.Label(label=subtitle_text, xalign=0)
            subtitle.add_css_class("dim-label")
            labels_box.append(title)
            labels_box.append(subtitle)
            row_box.append(labels_box)

            button_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
            edit_button = Gtk.Button(
                icon_name="document-edit-symbolic", css_classes=["flat"]
            )
            edit_button.connect("clicked", self._on_edit_port_forward_clicked, index)
            delete_button = Gtk.Button(
                icon_name="user-trash-symbolic", css_classes=["flat"]
            )
            delete_button.connect("clicked", self._on_delete_port_forward_clicked, index)
            button_box.append(edit_button)
            button_box.append(delete_button)
            row_box.append(button_box)

            row.set_child(row_box)
            self.port_forward_list.append(row)

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

    def _show_port_forward_dialog(self, existing: Optional[dict] = None) -> Optional[dict]:
        is_edit = existing is not None

        # Use Adw.Window with ToolbarView for consistency
        dialog = Adw.Window(
            transient_for=self,
            modal=True,
            default_width=600,
            default_height=600,
        )
        dialog.set_title(_("Edit Port Forward") if is_edit else _("Add Port Forward"))

        # Use Adw.ToolbarView for proper layout
        toolbar_view = Adw.ToolbarView()
        dialog.set_content(toolbar_view)

        # Header bar with buttons
        header_bar = Adw.HeaderBar()
        header_bar.set_show_end_title_buttons(True)
        header_bar.set_show_start_title_buttons(False)

        cancel_button = Gtk.Button(label=_("Cancel"))
        cancel_button.connect("clicked", lambda b: dialog.close())
        header_bar.pack_start(cancel_button)

        save_button = Gtk.Button(label=_("Save"), css_classes=["suggested-action"])
        header_bar.pack_end(save_button)

        toolbar_view.add_top_bar(header_bar)

        # Scrolled content with preferences page
        scrolled = Gtk.ScrolledWindow()
        scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        toolbar_view.set_content(scrolled)

        prefs_page = Adw.PreferencesPage()
        scrolled.set_child(prefs_page)

        # Tunnel settings group
        settings_group = Adw.PreferencesGroup()
        prefs_page.add(settings_group)

        # Name field - using Adw.EntryRow
        name_row = Adw.EntryRow(title=_("Tunnel Name"))
        name_row.set_text(existing.get("name", "") if existing else "")
        settings_group.add(name_row)

        # Local Settings group
        local_group = Adw.PreferencesGroup(
            title=_("Local Settings"),
        )
        prefs_page.add(local_group)

        # Local Host - using Adw.EntryRow
        local_host_row = Adw.EntryRow(title=_("Local Host"))
        local_host_row.set_text(
            existing.get("local_host", "localhost") if existing else "localhost"
        )
        local_group.add(local_host_row)

        # Local Port - using Adw.SpinRow
        local_port_row = Adw.SpinRow.new_with_range(1, 65535, 1)
        local_port_row.set_title(_("Local Port"))
        local_port_row.set_subtitle(_("Port on your machine (1025-65535 recommended)"))
        local_port_row.set_value(existing.get("local_port", 8080) if existing else 8080)
        local_group.add(local_port_row)

        # Remote Settings group
        remote_group = Adw.PreferencesGroup(
            title=_("Remote Settings"),
        )
        prefs_page.add(remote_group)

        # Remote host toggle
        use_custom_remote = bool(existing and existing.get("remote_host"))
        remote_toggle_row = Adw.SwitchRow(
            title=_("Use Custom Remote Host"),
            subtitle=_("Leave off to use the SSH server as target"),
        )
        remote_toggle_row.set_active(use_custom_remote)
        remote_group.add(remote_toggle_row)

        # Remote Host - using Adw.EntryRow
        remote_host_row = Adw.EntryRow(title=_("Remote Host"))
        remote_host_row.set_text(existing.get("remote_host", "") if existing else "")
        remote_host_row.set_visible(use_custom_remote)
        remote_group.add(remote_host_row)

        def on_remote_toggle(switch_row, _param):
            remote_host_row.set_visible(switch_row.get_active())

        remote_toggle_row.connect("notify::active", on_remote_toggle)

        # Remote Port - using Adw.SpinRow
        remote_port_row = Adw.SpinRow.new_with_range(1, 65535, 1)
        remote_port_row.set_title(_("Remote Port"))
        remote_port_row.set_subtitle(_("Port on the remote host (1-65535)"))
        remote_port_row.set_value(existing.get("remote_port", 80) if existing else 80)
        remote_group.add(remote_port_row)

        result: Optional[dict] = None

        def on_save(_button):
            nonlocal result
            name = name_row.get_text().strip() or _("Tunnel")
            local_host = local_host_row.get_text().strip() or "localhost"
            local_port = int(local_port_row.get_value())
            remote_port = int(remote_port_row.get_value())
            remote_host = (
                remote_host_row.get_text().strip()
                if remote_toggle_row.get_active()
                else ""
            )

            errors = []
            if not (1024 < local_port <= 65535):
                errors.append(
                    _(
                        "Local port must be between 1025 and 65535 (ports below 1024 require administrator privileges)."
                    )
                )
            if not (1 <= remote_port <= 65535):
                errors.append(_("Remote port must be between 1 and 65535."))
            if not local_host:
                errors.append(_("Local host cannot be empty."))

            if errors:
                self._show_error_dialog(
                    _("Invalid Port Forward"),
                    "\n".join(errors),
                )
                return

            result = {
                "name": name,
                "local_host": local_host,
                "local_port": local_port,
                "remote_host": remote_host,
                "remote_port": remote_port,
            }
            dialog.close()

        save_button.connect("clicked", on_save)

        # Run dialog blocking
        response_holder = {"finished": False}
        loop = GLib.MainLoop()

        def on_close(_dialog):
            response_holder["finished"] = True
            loop.quit()

        dialog.connect("close-request", on_close)
        dialog.present()
        loop.run()

        return result

    def _on_name_changed(self, entry) -> None:
        entry.remove_css_class("error")
        self._mark_changed()

    def _on_color_changed(self, button: Gtk.ColorButton) -> None:
        self._mark_changed()

    def _on_clear_color_clicked(self, button: Gtk.Button) -> None:
        self.color_button.set_rgba(Gdk.RGBA())  # Set to no color
        self._mark_changed()

    def _on_folder_changed(self, combo_row, param) -> None:
        self._mark_changed()

    def _on_type_changed(self, combo_row, param) -> None:
        if (
            combo_row.get_selected() == 1
            and self.is_new_item
            and self.auth_combo.get_selected() == 0
            and not self.key_path_entry.get_text().strip()
        ):
            self.key_path_entry.set_text(f"{get_ssh_directory()}/id_rsa")
        self._update_ssh_visibility()
        self._update_local_visibility()
        self._mark_changed()

    def _on_host_changed(self, entry: Gtk.Entry) -> None:
        entry.remove_css_class("error")
        self._mark_changed()
        hostname = entry.get_text().strip()
        if hostname and not HostnameValidator.is_valid_hostname(hostname):
            entry.add_css_class("error")

    def _on_user_changed(self, entry: Gtk.Entry) -> None:
        self._mark_changed()

    def _on_port_changed(self, spin_row, _param) -> None:
        self._mark_changed()
        port = int(spin_row.get_value())
        spin_row.remove_css_class(
            "error"
        ) if 1 <= port <= 65535 else spin_row.add_css_class("error")

    def _on_auth_changed(self, combo_row, param) -> None:
        if combo_row.get_selected() == 0 and not self.key_path_entry.get_text().strip():
            self.key_path_entry.set_text(f"{get_ssh_directory()}/id_rsa")
        self._update_auth_visibility()
        self._mark_changed()

    def _on_key_path_changed(self, entry: Gtk.Entry) -> None:
        entry.remove_css_class("error")
        self._mark_changed()
        key_path = entry.get_text().strip()
        if key_path:
            try:
                validate_ssh_key_file(key_path)
            except Exception:
                entry.add_css_class("error")

    def _on_password_changed(self, entry: Gtk.PasswordEntry) -> None:
        self._mark_changed()

    def _on_post_login_toggle(self, switch_row: Adw.SwitchRow, _param) -> None:
        self._mark_changed()
        self._update_post_login_command_state()
        if self.post_login_entry and not switch_row.get_active():
            self.post_login_entry.remove_css_class("error")

    def _on_post_login_command_changed(self, entry: Gtk.Entry) -> None:
        entry.remove_css_class("error")
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
            self.sftp_local_entry.remove_css_class("error")

    def _on_sftp_local_changed(self, entry: Gtk.Entry) -> None:
        entry.remove_css_class("error")
        self._mark_changed()

    def _on_sftp_remote_changed(self, entry: Gtk.Entry) -> None:
        self._mark_changed()

    def _update_ssh_visibility(self) -> None:
        if self.ssh_box and self.type_combo:
            is_ssh = self.type_combo.get_selected() == 1
            self.ssh_box.set_visible(is_ssh)
            if hasattr(self, "ssh_options_group") and self.ssh_options_group:
                self.ssh_options_group.set_visible(is_ssh)
            if hasattr(self, "test_button"):
                self.test_button.set_visible(is_ssh)
            if self.x11_switch:
                self.x11_switch.set_sensitive(is_ssh)
                if not is_ssh:
                    self.x11_switch.set_active(False)
            if self.sftp_switch:
                self.sftp_switch.set_sensitive(is_ssh)
                if not is_ssh:
                    self.sftp_switch.set_active(False)
        self._update_port_forward_state()
        self._update_post_login_command_state()
        self._update_sftp_state()

    def _update_local_visibility(self) -> None:
        """Update visibility of local terminal options based on session type."""
        if hasattr(self, "local_terminal_group") and self.local_terminal_group:
            is_local = self.type_combo.get_selected() == 0 if self.type_combo else False
            self.local_terminal_group.set_visible(is_local)

    def _update_auth_visibility(self) -> None:
        if self.key_box and self.password_box and self.auth_combo:
            is_key = self.auth_combo.get_selected() == 0
            self.key_box.set_visible(is_key)
            self.password_box.set_visible(not is_key)
        self._update_port_forward_state()
        self._update_post_login_command_state()
        self._update_sftp_state()

    def _update_port_forward_state(self) -> None:
        is_ssh_session = (
            self.type_combo.get_selected() == 1 if self.type_combo else False
        )
        # Port forward widgets are now inside SSH Options, control visibility
        if hasattr(self, "port_forward_add_row") and self.port_forward_add_row:
            self.port_forward_add_row.set_visible(is_ssh_session)
        if hasattr(self, "port_forward_list_row") and self.port_forward_list_row:
            self.port_forward_list_row.set_visible(is_ssh_session)
        if self.port_forward_list:
            self.port_forward_list.set_sensitive(is_ssh_session)
        if self.port_forward_add_button:
            self.port_forward_add_button.set_sensitive(is_ssh_session)

    def _update_post_login_command_state(self) -> None:
        if not self.post_login_switch or not self.post_login_entry:
            return
        if not hasattr(self, "post_login_command_row"):
            return
        is_ssh_session = (
            self.type_combo.get_selected() == 1 if self.type_combo else False
        )
        self.post_login_switch.set_sensitive(is_ssh_session)
        is_enabled = (
            self.post_login_switch.get_active() and is_ssh_session
        )
        # Control visibility instead of just sensitivity
        self.post_login_command_row.set_visible(is_enabled)
        if not is_enabled:
            self.post_login_entry.remove_css_class("error")

    def _update_sftp_state(self) -> None:
        if (
            not self.sftp_switch
            or not self.sftp_local_entry
            or not self.sftp_remote_entry
        ):
            return
        if not hasattr(self, "sftp_local_row") or not hasattr(self, "sftp_remote_row"):
            return
        is_ssh_session = (
            self.type_combo.get_selected() == 1 if self.type_combo else False
        )
        self.sftp_switch.set_sensitive(is_ssh_session)
        is_enabled = self.sftp_switch.get_active() and is_ssh_session
        # Control visibility instead of just sensitivity
        self.sftp_local_row.set_visible(is_enabled)
        self.sftp_remote_row.set_visible(is_enabled)
        if not is_enabled:
            self.sftp_local_entry.remove_css_class("error")

    def _on_local_working_dir_changed(self, entry: Gtk.Entry) -> None:
        """Handle changes to the local working directory."""
        self._mark_changed()
        path_text = entry.get_text().strip()
        if path_text:
            try:
                path = Path(path_text).expanduser()
                if not path.exists() or not path.is_dir():
                    entry.add_css_class("error")
                else:
                    entry.remove_css_class("error")
            except Exception:
                entry.add_css_class("error")
        else:
            entry.remove_css_class("error")

    def _on_local_startup_command_changed(self, buffer: Gtk.TextBuffer) -> None:
        """Handle changes to the local startup commands."""
        self._mark_changed()

    def _on_browse_working_dir_clicked(self, button) -> None:
        """Browse for a working directory folder."""
        try:
            file_dialog = Gtk.FileDialog(
                title=_("Select Working Directory"), modal=True
            )
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
                self.local_working_dir_entry.remove_css_class("error")
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
                    self.key_path_entry.remove_css_class("error")
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
                    _("Validation Error"),
                    _("Please fill in all required SSH fields first."),
                )
                return
            self.testing_dialog = Adw.MessageDialog(
                transient_for=self,
                title=_("Testing Connection..."),
                body=_("Attempting to connect to {host}...").format(
                    host=test_session.host
                ),
            )
            spinner = Gtk.Spinner(spinning=True, halign=Gtk.Align.CENTER, margin_top=12)
            self.testing_dialog.set_extra_child(spinner)
            self.testing_dialog.present()
            thread = threading.Thread(
                target=self._run_test_in_thread, args=(test_session,)
            )
            thread.start()
        except Exception as e:
            self.logger.error(f"Test connection setup failed: {e}")
            if hasattr(self, "testing_dialog"):
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
        return SessionItem(
            name="Test Connection",
            session_type="ssh",
            host=self.host_entry.get_text().strip(),
            user=self.user_entry.get_text().strip(),
            port=int(self.port_entry.get_value()),
            auth_type="key" if self.auth_combo.get_selected() == 0 else "password",
            auth_value=self._get_auth_value(),
        )

    def _run_test_in_thread(self, test_session: SessionItem):
        # Lazy import to defer loading until actually testing connection
        from ...terminal.spawner import get_spawner
        spawner = get_spawner()
        success, message = spawner.test_ssh_connection(test_session)
        GLib.idle_add(self._on_test_finished, success, message)

    def _on_test_finished(self, success: bool, message: str):
        if hasattr(self, "testing_dialog"):
            self.testing_dialog.close()
        if success:
            result_dialog = Adw.MessageDialog(
                transient_for=self,
                title=_("Connection Successful"),
                body=_("Successfully connected to the SSH server."),
            )
            result_dialog.add_response("ok", _("OK"))
            result_dialog.present()
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
                self.parent_window.refresh_tree()
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

        # Create a new SessionItem instance with the data from the form
        session_data = self.editing_session.to_dict()
        session_data.update({
            "name": self.name_row.get_text().strip(),
            "session_type": "local" if self.type_combo.get_selected() == 0 else "ssh",
        })

        rgba = self.color_button.get_rgba()
        if rgba.alpha > 0:  # Check if a color is set
            session_data["tab_color"] = rgba.to_string()
        else:
            session_data["tab_color"] = None

        if (
            hasattr(self, "folder_combo")
            and self.folder_combo
            and (selected_item := self.folder_combo.get_selected_item())
        ):
            session_data["folder_path"] = self.folder_paths_map.get(
                selected_item.get_string(), ""
            )

        post_login_enabled = (
            self.post_login_switch.get_active()
            if self.post_login_switch and self.type_combo.get_selected() == 1
            else False
        )
        post_login_command = (
            self.post_login_entry.get_text().strip()
            if self.post_login_entry
            else ""
        )
        session_data["post_login_command_enabled"] = post_login_enabled
        session_data["post_login_command"] = (
            post_login_command if post_login_enabled else ""
        )

        sftp_enabled = (
            self.sftp_switch.get_active()
            if self.sftp_switch and self.type_combo.get_selected() == 1
            else False
        )
        local_dir = (
            self.sftp_local_entry.get_text().strip()
            if self.sftp_local_entry
            else ""
        )
        remote_dir = (
            self.sftp_remote_entry.get_text().strip()
            if self.sftp_remote_entry
            else ""
        )
        session_data["sftp_session_enabled"] = sftp_enabled
        session_data["sftp_local_directory"] = local_dir
        session_data["sftp_remote_directory"] = remote_dir
        session_data["port_forwardings"] = (
            copy.deepcopy(self.port_forwardings)
            if session_data["session_type"] == "ssh"
            else []
        )
        session_data["x11_forwarding"] = (
            self.x11_switch.get_active()
            if self.x11_switch and session_data["session_type"] == "ssh"
            else False
        )

        raw_password = ""
        if session_data["session_type"] == "ssh":
            session_data.update({
                "host": self.host_entry.get_text().strip(),
                "user": self.user_entry.get_text().strip(),
                "port": int(self.port_entry.get_value()),
                "auth_type": "key"
                if self.auth_combo.get_selected() == 0
                else "password",
            })
            if session_data["auth_type"] == "key":
                session_data["auth_value"] = self.key_path_entry.get_text().strip()
            else:
                raw_password = self.password_entry.get_text()
                session_data["auth_value"] = ""  # Will be stored in keyring
            # Clear local terminal fields for SSH sessions
            session_data["local_working_directory"] = ""
            session_data["local_startup_command"] = ""
        else:
            session_data.update({
                "host": "",
                "user": "",
                "auth_type": "",
                "auth_value": "",
            })
            session_data["sftp_session_enabled"] = False
            session_data["port_forwardings"] = []
            session_data["x11_forwarding"] = False
            # Set local terminal fields
            session_data["local_working_directory"] = (
                self.local_working_dir_entry.get_text().strip()
                if hasattr(self, "local_working_dir_entry")
                else ""
            )
            # Get startup commands from TextView buffer
            startup_commands = ""
            if hasattr(self, "local_startup_command_view"):
                buffer = self.local_startup_command_view.get_buffer()
                startup_commands = buffer.get_text(
                    buffer.get_start_iter(), buffer.get_end_iter(), True
                ).strip()
            session_data["local_startup_command"] = startup_commands

        updated_session = SessionItem.from_dict(session_data)
        if updated_session.uses_password_auth() and raw_password:
            updated_session.auth_value = raw_password

        if not updated_session.validate():
            errors = updated_session.get_validation_errors()
            self._show_error_dialog(
                _("Validation Error"),
                _("Session validation failed:\n{}").format("\n".join(errors)),
            )
            return None

        return updated_session

    def _validate_basic_fields(self) -> bool:
        return self._validate_required_field(self.name_row, _("Session name"))

    def _validate_local_fields(self) -> bool:
        """Validate local terminal specific fields."""
        valid = True
        if hasattr(self, "local_working_dir_entry"):
            path_text = self.local_working_dir_entry.get_text().strip()
            if path_text:
                try:
                    path = Path(path_text).expanduser()
                    if not path.exists() or not path.is_dir():
                        self.local_working_dir_entry.add_css_class("error")
                        self._validation_errors.append(
                            _("Working directory must exist and be a folder.")
                        )
                        valid = False
                    else:
                        self.local_working_dir_entry.remove_css_class("error")
                except Exception:
                    self.local_working_dir_entry.add_css_class("error")
                    self._validation_errors.append(_("Invalid working directory path."))
                    valid = False
        if not valid and self._validation_errors:
            self._show_error_dialog(
                _("Validation Error"),
                "\n".join(self._validation_errors),
            )
        return valid

    def _validate_ssh_fields(self) -> bool:
        valid = True
        if not self._validate_required_field(self.host_entry, _("Host")):
            valid = False
        else:
            hostname = self.host_entry.get_text().strip()
            try:
                validate_ssh_hostname(hostname)
                self.host_entry.remove_css_class("error")
            except HostnameValidationError as e:
                self.host_entry.add_css_class("error")
                self._validation_errors.append(e.user_message)
                valid = False
        if self.auth_combo.get_selected() == 0:
            key_path = self.key_path_entry.get_text().strip()
            if key_path:
                try:
                    validate_ssh_key_file(key_path)
                    self.key_path_entry.remove_css_class("error")
                except SSHKeyError as e:
                    self.key_path_entry.add_css_class("error")
                    self._validation_errors.append(e.user_message)
                    valid = False
        if self.post_login_switch and self.post_login_entry:
            if (
                self.post_login_switch.get_active()
                and not self.post_login_entry.get_text().strip()
            ):
                self.post_login_entry.add_css_class("error")
                self._validation_errors.append(
                    _("Post-login command cannot be empty when enabled.")
                )
                valid = False
            else:
                self.post_login_entry.remove_css_class("error")
        if self.sftp_switch and self.sftp_switch.get_active():
            if self.sftp_local_entry:
                local_dir = self.sftp_local_entry.get_text().strip()
                if local_dir:
                    try:
                        local_path = Path(local_dir).expanduser()
                        if not local_path.exists() or not local_path.is_dir():
                            self.sftp_local_entry.add_css_class("error")
                            self._validation_errors.append(
                                _("SFTP local directory must exist and be a directory.")
                            )
                            valid = False
                        else:
                            self.sftp_local_entry.remove_css_class("error")
                    except Exception:
                        self.sftp_local_entry.add_css_class("error")
                        self._validation_errors.append(
                            _("SFTP local directory must exist and be a directory.")
                        )
                        valid = False
                else:
                    self.sftp_local_entry.remove_css_class("error")
        if not valid and self._validation_errors:
            self._show_error_dialog(
                _("SSH Validation Error"),
                _("SSH configuration errors:\n{}").format(
                    "\n".join(self._validation_errors)
                ),
            )
        return valid

    def _get_auth_value(self) -> str:
        if self.type_combo.get_selected() == 0:
            return ""
        elif self.auth_combo.get_selected() == 0:
            return self.key_path_entry.get_text().strip()
        else:
            return self.password_entry.get_text()
