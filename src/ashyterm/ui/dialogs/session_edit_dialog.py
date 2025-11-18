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
from ...terminal.spawner import get_spawner
from ...utils.crypto import is_encryption_available
from ...utils.exceptions import HostnameValidationError, SSHKeyError
from ...utils.platform import get_ssh_directory
from ...utils.security import (
    HostnameValidator,
    validate_ssh_hostname,
    validate_ssh_key_file,
)
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
        super().__init__(parent_window, title, default_width=860, default_height=680)

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
        self._original_data = self.editing_session.to_dict()
        self.folder_paths_map: dict[str, str] = {}
        self.post_login_switch: Optional[Adw.SwitchRow] = None
        self.post_login_entry: Optional[Gtk.Entry] = None
        self.sftp_group: Optional[Adw.PreferencesGroup] = None
        self.sftp_switch: Optional[Adw.SwitchRow] = None
        self.sftp_local_entry: Optional[Gtk.Entry] = None
        self.sftp_remote_entry: Optional[Gtk.Entry] = None
        self.port_forward_group: Optional[Adw.PreferencesGroup] = None
        self.port_forward_list: Optional[Gtk.ListBox] = None
        self.port_forward_add_button: Optional[Gtk.Button] = None
        self.port_forwardings: list[dict] = [
            dict(item) for item in self.editing_session.port_forwardings
        ]
        self.x11_switch: Optional[Adw.SwitchRow] = None
        self._setup_ui()
        self.connect("map", self._on_map)
        self.logger.info(
            f"Session edit dialog opened: {self.editing_session.name} ({'new' if self.is_new_item else 'edit'})"
        )

    def _on_map(self, widget):
        if self.name_entry:
            self.name_entry.grab_focus()

    def _setup_ui(self) -> None:
        try:
            main_box = Gtk.Box(
                orientation=Gtk.Orientation.VERTICAL,
                spacing=16,
                margin_top=24,
                margin_bottom=24,
                margin_start=24,
                margin_end=24,
            )
            self._create_name_section(main_box)
            self._create_appearance_section(main_box)
            if self.folder_store:
                self._create_folder_section(main_box)
            self._create_type_section(main_box)
            self._create_ssh_section(main_box)
            self._create_port_forward_section(main_box)
            action_bar = self._create_action_bar()
            content_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
            scrolled_window = Gtk.ScrolledWindow(vexpand=True, hexpand=True)
            scrolled_window.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
            scrolled_window.set_child(main_box)
            content_box.append(scrolled_window)
            content_box.append(action_bar)
            self.set_content(content_box)
            self._update_ssh_visibility()
            self._update_auth_visibility()
        except Exception as e:
            self.logger.error(f"Failed to setup UI: {e}")
            self._show_error_dialog(
                _("UI Error"), _("Failed to initialize dialog interface")
            )
            self.close()

    def _create_name_section(self, parent: Gtk.Box) -> None:
        name_group = Adw.PreferencesGroup(title=_("Session Information"))
        name_row = Adw.ActionRow(
            title=_("Session Name"), subtitle=_("A descriptive name for this session")
        )
        self.name_entry = Gtk.Entry(
            text=self.editing_session.name,
            placeholder_text=_("Enter session name..."),
            hexpand=True,
        )
        self.name_entry.connect("changed", self._on_name_changed)
        self.name_entry.connect("activate", self._on_save_clicked)
        name_row.add_suffix(self.name_entry)
        name_row.set_activatable_widget(self.name_entry)
        name_group.add(name_row)
        parent.append(name_group)

    def _create_appearance_section(self, parent: Gtk.Box) -> None:
        appearance_group = Adw.PreferencesGroup(title=_("Appearance"))
        color_row = Adw.ActionRow(
            title=_("Tab Color"),
            subtitle=_("Choose a color to identify this session's tab"),
        )

        color_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self.color_button = Gtk.ColorButton(valign=Gtk.Align.CENTER, show_editor=True)
        self.color_button.connect("color-set", self._on_color_changed)

        if self.editing_session.tab_color:
            rgba = Gdk.RGBA()
            if rgba.parse(self.editing_session.tab_color):
                self.color_button.set_rgba(rgba)
            else:
                self.color_button.set_rgba(Gdk.RGBA())  # Set to no color
        else:
            self.color_button.set_rgba(Gdk.RGBA())  # Set to no color

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
        color_row.set_activatable_widget(self.color_button)
        appearance_group.add(color_row)
        parent.append(appearance_group)

    def _create_folder_section(self, parent: Gtk.Box) -> None:
        folder_group = Adw.PreferencesGroup(title=_("Organization"))
        folder_row = Adw.ComboRow(
            title=_("Folder"), subtitle=_("Choose a folder to organize this session")
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
        parent.append(folder_group)

    def _create_type_section(self, parent: Gtk.Box) -> None:
        type_group = Adw.PreferencesGroup(title=_("Connection Type"))
        type_row = Adw.ComboRow(
            title=_("Session Type"),
            subtitle=_("Choose between local terminal or SSH connection"),
        )
        type_row.set_model(
            Gtk.StringList.new([_("Local Terminal"), _("SSH Connection")])
        )
        type_row.set_selected(0 if self.editing_session.is_local() else 1)
        type_row.connect("notify::selected", self._on_type_changed)
        self.type_combo = type_row
        type_group.add(type_row)
        parent.append(type_group)

    def _create_ssh_section(self, parent: Gtk.Box) -> None:
        ssh_group = Adw.PreferencesGroup(
            title=_("SSH Configuration"),
            description=_("Configure connection details for SSH sessions"),
        )
        host_row = Adw.ActionRow(
            title=_("Host"), subtitle=_("Hostname or IP address of the remote server")
        )
        self.host_entry = Gtk.Entry(
            text=self.editing_session.host,
            placeholder_text=_("example.com or 192.168.1.100"),
            hexpand=True,
        )
        self.host_entry.connect("changed", self._on_host_changed)
        self.host_entry.connect("activate", self._on_save_clicked)
        host_row.add_suffix(self.host_entry)
        host_row.set_activatable_widget(self.host_entry)
        ssh_group.add(host_row)
        user_row = Adw.ActionRow(
            title=_("Username"),
            subtitle=_("Username for SSH authentication (optional)"),
        )
        self.user_entry = Gtk.Entry(
            text=self.editing_session.user, placeholder_text=_("username"), hexpand=True
        )
        self.user_entry.connect("changed", self._on_user_changed)
        self.user_entry.connect("activate", self._on_save_clicked)
        user_row.add_suffix(self.user_entry)
        user_row.set_activatable_widget(self.user_entry)
        ssh_group.add(user_row)
        port_row = Adw.ActionRow(
            title=_("Port"), subtitle=_("SSH port number (default: 22)")
        )
        self.port_entry = Gtk.SpinButton.new_with_range(1, 65535, 1)
        self.port_entry.set_valign(Gtk.Align.CENTER)
        self.port_entry.set_value(self.editing_session.port)
        self.port_entry.connect("value-changed", self._on_port_changed)
        port_row.add_suffix(self.port_entry)
        port_row.set_activatable_widget(self.port_entry)
        ssh_group.add(port_row)
        auth_row = Adw.ComboRow(
            title=_("Authentication"), subtitle=_("Choose authentication method")
        )
        auth_row.set_model(Gtk.StringList.new([_("SSH Key"), _("Password")]))
        auth_row.set_selected(0 if self.editing_session.uses_key_auth() else 1)
        auth_row.connect("notify::selected", self._on_auth_changed)
        self.auth_combo = auth_row
        ssh_group.add(auth_row)
        self._create_ssh_key_section(ssh_group)
        self._create_password_section(ssh_group)
        self._create_post_login_command_section(ssh_group)
        self._create_x11_section(ssh_group)
        self.ssh_box = ssh_group
        parent.append(ssh_group)
        self._create_sftp_section(parent)

    def _create_ssh_key_section(self, parent: Adw.PreferencesGroup) -> None:
        key_row = Adw.ActionRow(
            title=_("SSH Key Path"), subtitle=_("Path to private SSH key file")
        )
        key_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        key_value = (
            self.editing_session.auth_value
            if self.editing_session.uses_key_auth()
            else ""
        )
        self.key_path_entry = Gtk.Entry(
            text=key_value,
            placeholder_text=f"{get_ssh_directory()}/id_rsa",
            hexpand=True,
        )
        self.key_path_entry.connect("changed", self._on_key_path_changed)
        self.key_path_entry.connect("activate", self._on_save_clicked)
        self.browse_button = Gtk.Button(label=_("Browse..."), css_classes=["flat"])
        self.browse_button.set_valign(Gtk.Align.CENTER)
        self.browse_button.connect("clicked", self._on_browse_key_clicked)
        key_box.append(self.key_path_entry)
        key_box.append(self.browse_button)
        key_row.add_suffix(key_box)
        key_row.set_activatable_widget(self.key_path_entry)
        self.key_box = key_row
        parent.add(key_row)

    def _create_password_section(self, parent: Adw.PreferencesGroup) -> None:
        password_row = Adw.ActionRow(
            title=_("Password"), subtitle=_("Password for SSH authentication")
        )
        if is_encryption_available():
            password_row.set_subtitle(
                _("Password for SSH authentication (stored in system keyring)")
            )
        else:
            password_row.set_subtitle(
                _("Password for SSH authentication (keyring not available)")
            )
        password_value = (
            self.editing_session.auth_value
            if self.editing_session.uses_password_auth()
            else ""
        )
        self.password_entry = Gtk.PasswordEntry(
            text=password_value,
            placeholder_text=_("Enter password..."),
            show_peek_icon=True,
            hexpand=True,
        )
        self.password_entry.connect("changed", self._on_password_changed)
        self.password_entry.connect("activate", self._on_save_clicked)
        password_row.add_suffix(self.password_entry)
        password_row.set_activatable_widget(self.password_entry)
        self.password_box = password_row
        parent.add(password_row)

    def _create_post_login_command_section(
        self, parent: Adw.PreferencesGroup
    ) -> None:
        toggle_row = Adw.SwitchRow(
            title=_("Run Command After Login"),
            subtitle=_(
                "Execute a custom command automatically after the SSH session connects"
            ),
        )
        toggle_row.set_active(self.editing_session.post_login_command_enabled)
        parent.add(toggle_row)

        command_row = Adw.ActionRow(
            title=_("Post-Login Command"),
            subtitle=_("Command to execute right after a successful login"),
        )
        self.post_login_entry = Gtk.Entry(
            text=self.editing_session.post_login_command,
            placeholder_text=_("Example: tmux attach -t main"),
            hexpand=True,
        )
        self.post_login_entry.connect(
            "changed", self._on_post_login_command_changed
        )
        self.post_login_entry.connect("activate", self._on_save_clicked)
        command_row.add_suffix(self.post_login_entry)
        command_row.set_activatable_widget(self.post_login_entry)
        parent.add(command_row)

        self.post_login_switch = toggle_row
        toggle_row.connect("notify::active", self._on_post_login_toggle)
        self._update_post_login_command_state()

    def _create_x11_section(self, parent: Adw.PreferencesGroup) -> None:
        x11_row = Adw.SwitchRow(
            title=_("Enable X11 Forwarding"),
            subtitle=_("Allow starting programs in graphical mode (X11)"),
        )
        x11_row.set_active(self.editing_session.x11_forwarding)
        x11_row.connect("notify::active", self._on_x11_toggled)
        parent.add(x11_row)
        self.x11_switch = x11_row

    def _create_port_forward_section(self, parent: Gtk.Box) -> None:
        group = Adw.PreferencesGroup(
            title=_("Port Forwarding"),
            description=_("Create SSH tunnels to forward local ports to remote targets"),
        )
        self.port_forward_group = group

        container = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)

        self.port_forward_list = Gtk.ListBox()
        self.port_forward_list.set_selection_mode(Gtk.SelectionMode.NONE)
        self.port_forward_list.add_css_class("boxed-list")
        container.append(self.port_forward_list)

        controls_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        controls_box.set_halign(Gtk.Align.START)
        add_button = Gtk.Button(
            icon_name="list-add-symbolic", label=_("Add"), css_classes=["flat"]
        )
        add_button.connect("clicked", self._on_add_port_forward_clicked)
        controls_box.append(add_button)
        self.port_forward_add_button = add_button
        container.append(controls_box)

        group.add(container)
        parent.append(group)

        self._refresh_port_forward_list()

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
        dialog = Gtk.Dialog(
            title=_("Edit Port Forward") if is_edit else _("Add Port Forward"),
            transient_for=self,
            modal=True,
        )
        dialog.add_buttons(
            _("Cancel"),
            Gtk.ResponseType.CANCEL,
            _("Save"),
            Gtk.ResponseType.OK,
        )
        dialog.set_default_response(Gtk.ResponseType.OK)

        content_area = dialog.get_content_area()
        content_area.set_spacing(12)
        content_area.set_margin_top(12)
        content_area.set_margin_bottom(12)
        content_area.set_margin_start(12)
        content_area.set_margin_end(12)

        grid = Gtk.Grid(column_spacing=12, row_spacing=12)
        content_area.append(grid)

        name_entry = Gtk.Entry(
            text=existing.get("name", "") if existing else "",
            placeholder_text=_("Tunnel name"),
            hexpand=True,
        )
        grid.attach(Gtk.Label(label=_("Name"), xalign=0), 0, 0, 1, 1)
        grid.attach(name_entry, 1, 0, 1, 1)

        local_host_entry = Gtk.Entry(
            text=existing.get("local_host", "localhost") if existing else "localhost",
            placeholder_text="localhost",
            hexpand=True,
        )
        grid.attach(Gtk.Label(label=_("Local Host"), xalign=0), 0, 1, 1, 1)
        grid.attach(local_host_entry, 1, 1, 1, 1)

        local_port_spin = Gtk.SpinButton.new_with_range(1, 65535, 1)
        local_port_spin.set_value(existing.get("local_port", 0) if existing else 0)
        grid.attach(Gtk.Label(label=_("Local Port"), xalign=0), 0, 2, 1, 1)
        grid.attach(local_port_spin, 1, 2, 1, 1)

        remote_host_entry = Gtk.Entry(
            text=existing.get("remote_host", "") if existing else "",
            placeholder_text=_("Leave blank to use the SSH host"),
            hexpand=True,
        )
        use_custom_remote = bool(existing and existing.get("remote_host"))
        remote_host_entry.set_sensitive(use_custom_remote)

        remote_host_toggle = Gtk.CheckButton(
            label=_("Connect to a different remote host"),
            active=use_custom_remote,
        )

        def on_remote_host_toggle(button):
            remote_host_entry.set_sensitive(button.get_active())

        remote_host_toggle.connect("toggled", on_remote_host_toggle)

        grid.attach(remote_host_toggle, 0, 3, 2, 1)
        grid.attach(Gtk.Label(label=_("Remote Host"), xalign=0), 0, 4, 1, 1)
        grid.attach(remote_host_entry, 1, 4, 1, 1)

        remote_port_spin = Gtk.SpinButton.new_with_range(1, 65535, 1)
        remote_port_spin.set_value(existing.get("remote_port", 0) if existing else 0)
        grid.attach(Gtk.Label(label=_("Remote Port"), xalign=0), 0, 5, 1, 1)
        grid.attach(remote_port_spin, 1, 5, 1, 1)

        result: Optional[dict] = None

        def run_dialog_blocking(dlg: Gtk.Dialog) -> int:
            response_holder = {"id": Gtk.ResponseType.CANCEL}
            loop = GLib.MainLoop()

            def on_response(_dlg, response_id):
                response_holder["id"] = response_id
                loop.quit()

            handler_id = dlg.connect("response", on_response)
            dlg.present()
            loop.run()
            dlg.disconnect(handler_id)
            dlg.hide()
            return response_holder["id"]

        while True:
            response = run_dialog_blocking(dialog)
            if response != Gtk.ResponseType.OK:
                break

            name = name_entry.get_text().strip() or _("Tunnel")
            local_host = local_host_entry.get_text().strip() or "localhost"
            local_port = int(local_port_spin.get_value())
            remote_port = int(remote_port_spin.get_value())
            remote_host = (
                remote_host_entry.get_text().strip()
                if remote_host_toggle.get_active()
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
                continue

            result = {
                "name": name,
                "local_host": local_host,
                "local_port": local_port,
                "remote_host": remote_host,
                "remote_port": remote_port,
            }
            break

        dialog.destroy()
        return result

    def _create_sftp_section(self, parent: Gtk.Box) -> None:
        sftp_group = Adw.PreferencesGroup(
            title=_("SFTP Session"),
            description=_(
                "Configure default directories for SFTP connections (optional)"
            ),
        )

        toggle_row = Adw.SwitchRow(
            title=_("Enable SFTP Session"),
            subtitle=_(
                "Use these directories when opening an SFTP tab for this session"
            ),
        )
        toggle_row.set_active(self.editing_session.sftp_session_enabled)
        sftp_group.add(toggle_row)
        self.sftp_switch = toggle_row
        toggle_row.connect("notify::active", self._on_sftp_toggle)

        local_row = Adw.ActionRow(
            title=_("Local Directory"),
            subtitle=_("Starting directory on your machine for SFTP"),
        )
        self.sftp_local_entry = Gtk.Entry(
            text=self.editing_session.sftp_local_directory,
            placeholder_text=_("Example: /home/user/projects"),
            hexpand=True,
        )
        self.sftp_local_entry.connect("changed", self._on_sftp_local_changed)
        self.sftp_local_entry.connect("activate", self._on_save_clicked)
        local_row.add_suffix(self.sftp_local_entry)
        local_row.set_activatable_widget(self.sftp_local_entry)
        sftp_group.add(local_row)

        remote_row = Adw.ActionRow(
            title=_("Remote Directory"),
            subtitle=_("Starting directory on the remote host for SFTP"),
        )
        self.sftp_remote_entry = Gtk.Entry(
            text=self.editing_session.sftp_remote_directory,
            placeholder_text=_("Example: /var/www"),
            hexpand=True,
        )
        self.sftp_remote_entry.connect("changed", self._on_sftp_remote_changed)
        self.sftp_remote_entry.connect("activate", self._on_save_clicked)
        remote_row.add_suffix(self.sftp_remote_entry)
        remote_row.set_activatable_widget(self.sftp_remote_entry)
        sftp_group.add(remote_row)

        parent.append(sftp_group)
        self.sftp_group = sftp_group
        self._update_sftp_state()

    def _create_action_bar(self) -> Gtk.ActionBar:
        action_bar = Gtk.ActionBar()
        cancel_button = Gtk.Button(label=_("Cancel"))
        cancel_button.set_valign(Gtk.Align.CENTER)
        cancel_button.connect("clicked", self._on_cancel_clicked)
        action_bar.pack_start(cancel_button)
        self.test_button = Gtk.Button(label=_("Test Connection"), css_classes=["flat"])
        self.test_button.set_valign(Gtk.Align.CENTER)
        self.test_button.connect("clicked", self._on_test_connection_clicked)
        action_bar.pack_start(self.test_button)
        save_button = Gtk.Button(label=_("Save"), css_classes=["suggested-action"])
        save_button.set_valign(Gtk.Align.CENTER)
        save_button.connect("clicked", self._on_save_clicked)
        action_bar.pack_end(save_button)
        self.set_default_widget(save_button)
        return action_bar

    def _on_name_changed(self, entry: Gtk.Entry) -> None:
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
        self._mark_changed()

    def _on_host_changed(self, entry: Gtk.Entry) -> None:
        entry.remove_css_class("error")
        self._mark_changed()
        hostname = entry.get_text().strip()
        if hostname and not HostnameValidator.is_valid_hostname(hostname):
            entry.add_css_class("error")

    def _on_user_changed(self, entry: Gtk.Entry) -> None:
        self._mark_changed()

    def _on_port_changed(self, spin_button: Gtk.SpinButton) -> None:
        self._mark_changed()
        port = int(spin_button.get_value())
        spin_button.remove_css_class(
            "error"
        ) if 1 <= port <= 65535 else spin_button.add_css_class("error")

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
            if hasattr(self, "test_button"):
                self.test_button.set_visible(is_ssh)
            if self.x11_switch:
                self.x11_switch.set_sensitive(is_ssh)
                if not is_ssh:
                    self.x11_switch.set_active(False)
            if self.sftp_group:
                self.sftp_group.set_visible(is_ssh)
        self._update_port_forward_state()
        self._update_post_login_command_state()
        self._update_sftp_state()

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
        if self.port_forward_group:
            self.port_forward_group.set_visible(is_ssh_session)
        if self.port_forward_list:
            self.port_forward_list.set_sensitive(is_ssh_session)
        if self.port_forward_add_button:
            self.port_forward_add_button.set_sensitive(is_ssh_session)

    def _update_post_login_command_state(self) -> None:
        if not self.post_login_switch or not self.post_login_entry:
            return
        is_ssh_session = (
            self.type_combo.get_selected() == 1 if self.type_combo else False
        )
        self.post_login_switch.set_sensitive(is_ssh_session)
        is_enabled = (
            self.post_login_switch.get_active() and is_ssh_session
        )
        self.post_login_entry.set_sensitive(is_enabled)
        if not is_enabled:
            self.post_login_entry.remove_css_class("error")

    def _update_sftp_state(self) -> None:
        if (
            not self.sftp_switch
            or not self.sftp_local_entry
            or not self.sftp_remote_entry
        ):
            return
        is_ssh_session = (
            self.type_combo.get_selected() == 1 if self.type_combo else False
        )
        self.sftp_switch.set_sensitive(is_ssh_session)
        is_enabled = self.sftp_switch.get_active() and is_ssh_session
        self.sftp_local_entry.set_sensitive(is_enabled)
        self.sftp_remote_entry.set_sensitive(is_enabled)
        if not is_enabled:
            self.sftp_local_entry.remove_css_class("error")

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
        if self.type_combo.get_selected() == 1 and not self._validate_ssh_fields():
            return None

        # Create a new SessionItem instance with the data from the form
        session_data = self.editing_session.to_dict()
        session_data.update({
            "name": self.name_entry.get_text().strip(),
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
        return self._validate_required_field(self.name_entry, _("Session name"))

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
