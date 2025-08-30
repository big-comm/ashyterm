# ashyterm/ui/dialogs.py

import threading
from typing import Any, Callable, Dict, List, Optional

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gio, GLib, GObject, Gtk, Vte

from ..helpers import accelerator_to_label
from ..sessions.models import SessionFolder, SessionItem
from ..sessions.operations import SessionOperations
from ..sessions.storage import save_sessions_and_folders
from ..settings.config import ColorSchemeMap, ColorSchemes, get_config_paths
from ..settings.manager import SettingsManager
from ..terminal.spawner import get_spawner
from ..utils.backup import BackupType, get_backup_manager
from ..utils.crypto import is_encryption_available
from ..utils.exceptions import HostnameValidationError, SSHKeyError
from ..utils.logger import get_logger, log_session_event
from ..utils.platform import get_ssh_directory, normalize_path
from ..utils.security import (
    HostnameValidator,
    validate_ssh_hostname,
    validate_ssh_key_file,
)
from ..utils.translation_utils import _


class BaseDialog(Adw.Window):
    """Base dialog class with enhanced functionality and error handling."""

    def __init__(self, parent_window, dialog_title: str, **kwargs):
        default_props = {
            "title": dialog_title,
            "modal": True,
            "transient_for": parent_window,
            "hide_on_close": True,
            "default_width": 450,
            "default_height": 500,
        }
        default_props.update(kwargs)
        super().__init__(**default_props)

        self.logger = get_logger(
            f"ashyterm.ui.dialogs.{self.__class__.__name__.lower()}"
        )
        self.parent_window = parent_window
        self.config_paths = get_config_paths()
        self._validation_errors: List[str] = []
        self._has_changes = False

        key_controller = Gtk.EventControllerKey()
        key_controller.connect("key-pressed", self._on_key_pressed)
        self.add_controller(key_controller)

    def _on_key_pressed(self, _controller, keyval, _keycode, _state):
        if keyval == Gdk.KEY_Escape:
            self._on_cancel_clicked(None)
            return Gdk.EVENT_STOP
        return Gdk.EVENT_PROPAGATE

    def _on_cancel_clicked(self, _button):
        self.close()

    def _mark_changed(self):
        self._has_changes = True

    def _show_error_dialog(
        self, title: str, message: str, details: Optional[str] = None
    ) -> None:
        try:
            dialog = Adw.MessageDialog(transient_for=self, title=title, body=message)
            if details:
                dialog.set_body_use_markup(True)
                full_body = (
                    f"{message}\n\n<small>{GLib.markup_escape_text(details)}</small>"
                )
                dialog.set_body(full_body)
            dialog.add_response("ok", _("OK"))
            dialog.present()
            self.logger.warning(f"Error dialog shown: {title} - {message}")
        except Exception as e:
            self.logger.error(f"Failed to show error dialog: {e}")

    def _show_warning_dialog(
        self, title: str, message: str, on_confirm: Optional[Callable] = None
    ) -> None:
        try:
            dialog = Adw.MessageDialog(transient_for=self, title=title, body=message)
            dialog.add_response("cancel", _("Cancel"))
            dialog.add_response("confirm", _("Continue"))
            dialog.set_response_appearance(
                "confirm", Adw.ResponseAppearance.DESTRUCTIVE
            )

            def on_response(dlg, response_id):
                if response_id == "confirm" and on_confirm:
                    on_confirm()
                dlg.close()

            dialog.connect("response", on_response)
            dialog.present()
        except Exception as e:
            self.logger.error(f"Failed to show warning dialog: {e}")

    def _validate_required_field(self, entry: Gtk.Entry, field_name: str) -> bool:
        value = entry.get_text().strip()
        if not value:
            entry.add_css_class("error")
            self._validation_errors.append(_("{} is required").format(field_name))
            return False
        else:
            entry.remove_css_class("error")
            return True

    def _clear_validation_errors(self):
        self._validation_errors.clear()


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
        self.editing_session = (
            SessionItem.from_dict(session_item.to_dict())
            if not self.is_new_item
            else session_item
        )
        self.original_session = session_item if not self.is_new_item else None
        self.folder_paths_map: Dict[str, str] = {}
        self._setup_ui()
        self.connect("map", self._on_map)
        self.logger.info(
            f"Session edit dialog opened: {self.editing_session.name} ({'new' if self.is_new_item else 'edit'})"
        )

    def _on_map(self, _widget):
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
            if self.folder_store:
                self._create_folder_section(main_box)
            self._create_type_section(main_box)
            self._create_ssh_section(main_box)
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
                if isinstance(self.folder_store.get_item(i), SessionFolder)
            ],
            key=lambda f: f.path,
        )
        for folder in folders:
            display_name = f"{'  ' * folder.path.count('/')}{folder.name}"
            folder_model.append(display_name)
            self.folder_paths_map[display_name] = folder.path
        folder_row.set_model(folder_model)
        selected_index = 0
        for i, (_display, path_val) in enumerate(self.folder_paths_map.items()):
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
        self.ssh_box = ssh_group
        parent.append(ssh_group)

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

    def _create_action_bar(self) -> Gtk.ActionBar:
        action_bar = Gtk.ActionBar()
        cancel_button = Gtk.Button(label=_("Cancel"))
        cancel_button.connect("clicked", self._on_cancel_clicked)
        action_bar.pack_start(cancel_button)
        self.test_button = Gtk.Button(label=_("Test Connection"), css_classes=["flat"])
        self.test_button.connect("clicked", self._on_test_connection_clicked)
        action_bar.pack_start(self.test_button)
        save_button = Gtk.Button(label=_("Save"), css_classes=["suggested-action"])
        save_button.connect("clicked", self._on_save_clicked)
        action_bar.pack_end(save_button)
        self.set_default_widget(save_button)
        return action_bar

    def _on_name_changed(self, entry: Gtk.Entry) -> None:
        entry.remove_css_class("error")
        self._mark_changed()

    def _on_folder_changed(self, _combo_row, _param) -> None:
        self._mark_changed()

    def _on_type_changed(self, combo_row, _param) -> None:
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

    def _on_user_changed(self, _entry: Gtk.Entry) -> None:
        self._mark_changed()

    def _on_port_changed(self, spin_button: Gtk.SpinButton) -> None:
        self._mark_changed()
        port = int(spin_button.get_value())
        spin_button.remove_css_class(
            "error"
        ) if 1 <= port <= 65535 else spin_button.add_css_class("error")

    def _on_auth_changed(self, combo_row, _param) -> None:
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

    def _on_password_changed(self, _entry: Gtk.PasswordEntry) -> None:
        self._mark_changed()

    def _update_ssh_visibility(self) -> None:
        if self.ssh_box and self.type_combo:
            is_ssh = self.type_combo.get_selected() == 1
            self.ssh_box.set_visible(is_ssh)
            if hasattr(self, "test_button"):
                self.test_button.set_visible(is_ssh)

    def _update_auth_visibility(self) -> None:
        if self.key_box and self.password_box and self.auth_combo:
            is_key = self.auth_combo.get_selected() == 0
            self.key_box.set_visible(is_key)
            self.password_box.set_visible(not is_key)

    def _on_browse_key_clicked(self, _button) -> None:
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

    def _on_test_connection_clicked(self, _button) -> None:
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

    def _on_cancel_clicked(self, _button) -> None:
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

    def _on_save_clicked(self, _button) -> None:
        try:
            if not self._validate_and_save():
                return
            try:
                save_sessions_and_folders(self.session_store, self.folder_store)
                self._create_save_backup()
                log_session_event(
                    "created" if self.is_new_item else "modified",
                    self.editing_session.name,
                )
                if hasattr(self.parent_window, "refresh_tree"):
                    self.parent_window.refresh_tree()
                self.logger.info(
                    f"Session {'created' if self.is_new_item else 'updated'}: {self.editing_session.name}"
                )
                self.close()
            except Exception as e:
                self.logger.error(f"Failed to save session: {e}")
                self._show_error_dialog(
                    _("Save Error"), _("Failed to save session: {}").format(e)
                )
        except Exception as e:
            self.logger.error(f"Save handling failed: {e}")
            self._show_error_dialog(
                _("Save Error"), _("An unexpected error occurred while saving")
            )

    def _validate_and_save(self) -> bool:
        try:
            self._clear_validation_errors()
            if not self._validate_basic_fields():
                return False
            if self.type_combo.get_selected() == 1 and not self._validate_ssh_fields():
                return False
            self._apply_changes_to_session()
            if not self.editing_session.validate():
                errors = self.editing_session.get_validation_errors()
                self._show_error_dialog(
                    _("Validation Error"),
                    _("Session validation failed:\n{}").format("\n".join(errors)),
                )
                return False
            if self.is_new_item:
                self.session_store.append(self.editing_session)
            else:
                self._update_original_session()
            return True
        except Exception as e:
            self.logger.error(f"Validation and save failed: {e}")
            self._show_error_dialog(
                _("Validation Error"), _("Failed to validate session: {}").format(e)
            )
            return False

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

    def _apply_changes_to_session(self) -> None:
        self.editing_session.name = self.name_entry.get_text().strip()
        self.editing_session.session_type = (
            "local" if self.type_combo.get_selected() == 0 else "ssh"
        )
        if self.folder_combo and (
            selected_item := self.folder_combo.get_selected_item()
        ):
            self.editing_session.folder_path = self.folder_paths_map.get(
                selected_item.get_string(), ""
            )
        if self.editing_session.is_ssh():
            self.editing_session.host = self.host_entry.get_text().strip()
            self.editing_session.user = self.user_entry.get_text().strip()
            self.editing_session.port = int(self.port_entry.get_value())
            self.editing_session.auth_type = (
                "key" if self.auth_combo.get_selected() == 0 else "password"
            )
            self.editing_session.auth_value = self._get_auth_value()
        else:
            self.editing_session.host = ""
            self.editing_session.user = ""
            self.editing_session.auth_type = ""
            self.editing_session.auth_value = ""

    def _update_original_session(self) -> None:
        self.original_session.name = self.editing_session.name
        self.original_session.session_type = self.editing_session.session_type
        self.original_session.host = self.editing_session.host
        self.original_session.user = self.editing_session.user
        self.original_session.port = self.editing_session.port
        self.original_session.auth_type = self.editing_session.auth_type
        self.original_session.auth_value = self.editing_session.auth_value
        self.original_session.folder_path = self.editing_session.folder_path

    def _create_save_backup(self) -> None:
        try:
            backup_manager = get_backup_manager()
            if backup_manager and self.config_paths.SESSIONS_FILE.exists():
                backup_manager.create_backup(
                    [self.config_paths.SESSIONS_FILE],
                    BackupType.AUTOMATIC,
                    _("Session {} {}").format(
                        "created" if self.is_new_item else "modified",
                        self.editing_session.name,
                    ),
                )
        except Exception as e:
            self.logger.warning(f"Failed to create session backup: {e}")


class FolderEditDialog(BaseDialog):
    # This class remains unchanged
    def __init__(
        self,
        parent_window,
        folder_store,
        folder_item: Optional[SessionFolder] = None,
        position: Optional[int] = None,
        is_new: bool = False,
    ):
        self.is_new_item = is_new
        title = _("Add Folder") if self.is_new_item else _("Edit Folder")
        super().__init__(parent_window, title, default_width=420, default_height=380)
        self.folder_store = folder_store
        self.original_folder = folder_item if not self.is_new_item else None
        self.editing_folder = (
            SessionFolder.from_dict(folder_item.to_dict())
            if not self.is_new_item
            else folder_item
        )
        self.position = position
        self.old_path = folder_item.path if folder_item else None
        self.parent_paths_map: Dict[str, str] = {}
        self._setup_ui()
        self.connect("map", self._on_map)
        self.logger.info(
            f"Folder edit dialog opened: {self.editing_folder.name} ({'new' if self.is_new_item else 'edit'})"
        )

    def _on_map(self, _widget):
        if self.name_entry:
            self.name_entry.grab_focus()

    def _setup_ui(self) -> None:
        try:
            main_box = Gtk.Box(
                orientation=Gtk.Orientation.VERTICAL,
                spacing=20,
                margin_top=24,
                margin_bottom=24,
                margin_start=24,
                margin_end=24,
            )
            folder_group = Adw.PreferencesGroup(title=_("Folder Information"))
            self._create_name_row(folder_group)
            self._create_parent_row(folder_group)
            main_box.append(folder_group)
            action_bar = self._create_action_bar()
            content_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
            content_box.append(main_box)
            content_box.append(action_bar)
            self.set_content(content_box)
        except Exception as e:
            self.logger.error(f"Failed to setup UI: {e}")
            self._show_error_dialog(
                _("UI Error"), _("Failed to initialize dialog interface")
            )
            self.close()

    def _create_name_row(self, parent: Adw.PreferencesGroup) -> None:
        name_row = Adw.ActionRow(
            title=_("Folder Name"),
            subtitle=_("A descriptive name for organizing sessions"),
        )
        self.name_entry = Gtk.Entry(
            text=self.editing_folder.name,
            placeholder_text=_("Enter folder name..."),
            hexpand=True,
        )
        self.name_entry.connect("changed", self._on_name_changed)
        self.name_entry.connect("activate", self._on_save_clicked)
        name_row.add_suffix(self.name_entry)
        name_row.set_activatable_widget(self.name_entry)
        parent.add(name_row)

    def _create_parent_row(self, parent: Adw.PreferencesGroup) -> None:
        parent_row = Adw.ComboRow(
            title=_("Parent Folder"),
            subtitle=_("Choose a parent folder for organization"),
        )
        parent_model = Gtk.StringList()
        parent_model.append(_("Root"))
        self.parent_paths_map = {_("Root"): ""}
        folders = sorted(
            [
                self.folder_store.get_item(i)
                for i in range(self.folder_store.get_n_items())
            ],
            key=lambda f: f.path,
        )
        selected_index = 0
        for folder in folders:
            display_name = f"{'  ' * folder.path.count('/')}{folder.name}"
            parent_model.append(display_name)
            self.parent_paths_map[display_name] = folder.path
            if self.editing_folder and folder.path == self.editing_folder.parent_path:
                selected_index = parent_model.get_n_items() - 1
        parent_row.set_model(parent_model)
        parent_row.set_selected(selected_index)
        parent_row.connect("notify::selected", self._on_parent_changed)
        self.parent_combo = parent_row
        parent.add(parent_row)

    def _create_action_bar(self) -> Gtk.ActionBar:
        action_bar = Gtk.ActionBar()
        cancel_button = Gtk.Button(label=_("Cancel"))
        cancel_button.connect("clicked", self._on_cancel_clicked)
        action_bar.pack_start(cancel_button)
        save_button = Gtk.Button(label=_("Save"), css_classes=["suggested-action"])
        save_button.connect("clicked", self._on_save_clicked)
        action_bar.pack_end(save_button)
        self.set_default_widget(save_button)
        return action_bar

    def _on_name_changed(self, entry: Gtk.Entry) -> None:
        entry.remove_css_class("error")
        self._mark_changed()

    def _on_parent_changed(self, _combo_row, _param) -> None:
        self._mark_changed()

    def _on_cancel_clicked(self, _button) -> None:
        if self._has_changes:
            self._show_warning_dialog(
                _("Unsaved Changes"),
                _("You have unsaved changes. Are you sure you want to cancel?"),
                lambda: self.close(),
            )
        else:
            self.close()

    def _on_save_clicked(self, _button) -> None:
        try:
            operations = self.parent_window.session_tree.operations
            updated_folder = self._build_updated_folder()
            if not updated_folder:
                return
            result = (
                operations.add_folder(updated_folder)
                if self.is_new_item
                else operations.update_folder(self.position, updated_folder)
            )
            if result and result.success:
                self.logger.info(
                    f"Folder {'created' if self.is_new_item else 'updated'}: {updated_folder.name}"
                )
                self.parent_window.refresh_tree()
                self.close()
            elif result:
                self._show_error_dialog(_("Save Error"), result.message)
        except Exception as e:
            self.logger.error(f"Save handling failed: {e}")
            self._show_error_dialog(
                _("Save Error"), _("Failed to save folder: {}").format(e)
            )

    def _build_updated_folder(self) -> Optional[SessionFolder]:
        self._clear_validation_errors()
        if not self._validate_required_field(self.name_entry, _("Folder name")):
            return None
        name = self.name_entry.get_text().strip()
        selected_item = self.parent_combo.get_selected_item()
        parent_path = ""
        if selected_item:
            parent_path = self.parent_paths_map.get(selected_item.get_string(), "")
        new_path = normalize_path(
            f"{parent_path}/{name}" if parent_path else f"/{name}"
        )
        updated_data = self.editing_folder.to_dict()
        updated_data.update({
            "name": name,
            "parent_path": parent_path,
            "path": str(new_path),
        })
        return SessionFolder.from_dict(updated_data)


class _PalettePreview(Gtk.CheckButton):
    """A custom widget to display a color scheme preview."""

    def __init__(self, scheme_data, **kwargs):
        super().__init__(**kwargs)
        self.set_can_focus(False)
        self.add_css_class("card")
        self.add_css_class("palette-preview")

        main_vbox = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=8,
            margin_top=12,
            margin_bottom=12,
            margin_start=12,
            margin_end=12,
        )
        self.set_child(main_vbox)

        label = Gtk.Label(
            label=f"The quick brown fox\njumps over the lazy dog",
            justify=Gtk.Justification.LEFT,
            xalign=0,
        )
        main_vbox.append(label)

        color_box = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL,
            spacing=4,
            halign=Gtk.Align.START,
            margin_top=4,
        )
        main_vbox.append(color_box)

        for i in range(8):
            color_widget = Gtk.Box()
            color_widget.set_size_request(16, 16)
            color_widget.add_css_class(f"color-swatch-{i}")
            color_box.append(color_widget)

        self.provider = Gtk.CssProvider()
        style_context = self.get_style_context()
        style_context.add_provider(self.provider, Gtk.STYLE_PROVIDER_PRIORITY_USER)
        self._update_colors(scheme_data)

    def _update_colors(self, scheme_data):
        fg = scheme_data["foreground"]
        bg = scheme_data["background"]
        palette = scheme_data["palette"]
        css = f"""
        .palette-preview.card {{
            background-color: {bg};
            color: {fg};
        }}
        """
        for i in range(8):
            if i < len(palette):
                css += f".color-swatch-{i} {{ background-color: {palette[i]}; border-radius: 3px; }}"
        self.provider.load_from_data(css.encode("utf-8"))


class PreferencesDialog(Adw.PreferencesWindow):
    """Enhanced preferences dialog with comprehensive settings management."""

    __gsignals__ = {
        "color-scheme-changed": (GObject.SignalFlags.RUN_FIRST, None, (int,)),
        "transparency-changed": (GObject.SignalFlags.RUN_FIRST, None, (float,)),
        "font-changed": (GObject.SignalFlags.RUN_FIRST, None, (str,)),
        "shortcut-changed": (GObject.SignalFlags.RUN_FIRST, None, ()),
        "setting-changed": (GObject.SignalFlags.RUN_FIRST, None, (str, object)),
    }

    def __init__(self, parent_window, settings_manager: SettingsManager):
        super().__init__(
            title=_("Preferences"),
            transient_for=parent_window,
            modal=True,
            hide_on_close=True,
            default_width=600,
            default_height=700,
        )
        self.logger = get_logger("ashyterm.ui.dialogs.preferences")
        self.settings_manager = settings_manager
        self.shortcut_rows: Dict[str, Adw.ActionRow] = {}
        self._setup_appearance_page()
        self._setup_behavior_page()
        self._setup_shortcuts_page()
        self._setup_advanced_page()
        self.logger.info("Preferences dialog initialized")

    def _setup_appearance_page(self) -> None:
        page = Adw.PreferencesPage(
            title=_("Appearance"), icon_name="preferences-desktop-display-symbolic"
        )
        self.add(page)

        palette_group = Adw.PreferencesGroup(title=_("Palette"))
        page.add(palette_group)

        flowbox = Gtk.FlowBox()
        flowbox.set_valign(Gtk.Align.START)
        flowbox.set_max_children_per_line(3)
        flowbox.set_min_children_per_line(2)
        flowbox.set_selection_mode(Gtk.SelectionMode.NONE)
        flowbox.set_homogeneous(True)

        schemes = ColorSchemes.get_schemes()
        scheme_order = ColorSchemeMap.get_schemes_list()
        current_selection_index = self.settings_manager.get("color_scheme", 0)

        first_button = None
        for i, scheme_key in enumerate(scheme_order):
            scheme_data = schemes[scheme_key]
            preview = _PalettePreview(scheme_data=scheme_data)
            if first_button is None:
                first_button = preview
            else:
                preview.set_group(first_button)

            if i == current_selection_index:
                preview.set_active(True)

            preview.connect("toggled", self._on_palette_selected, i)
            flowbox.insert(preview, -1)

        palette_row = Adw.PreferencesRow()
        palette_row.set_child(flowbox)
        palette_group.add(palette_row)

        font_group = Adw.PreferencesGroup(
            title=_("Typography"),
            description=_("Configure fonts and spacing"),
        )
        page.add(font_group)

        font_row = Adw.ActionRow(
            title=_("Terminal Font"),
            subtitle=_("Select font family and size for terminal text"),
        )
        font_button = Gtk.FontButton()
        font_button.set_font(self.settings_manager.get("font", "Monospace 10"))
        font_button.connect("font-set", self._on_font_changed)
        font_row.add_suffix(font_button)
        font_row.set_activatable_widget(font_button)
        font_group.add(font_row)

        line_spacing_row = Adw.ActionRow(
            title=_("Line Spacing"),
            subtitle=_("Adjust the vertical space between lines"),
        )
        spacing_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self.spacing_spin = Gtk.SpinButton.new_with_range(0.8, 2.0, 0.05)
        self.spacing_spin.set_value(self.settings_manager.get("line_spacing", 1.0))
        self.spacing_spin.connect("value-changed", self._on_line_spacing_changed)
        spacing_box.append(self.spacing_spin)
        line_spacing_row.add_suffix(spacing_box)
        line_spacing_row.set_activatable_widget(self.spacing_spin)
        font_group.add(line_spacing_row)

        misc_group = Adw.PreferencesGroup(title=_("Miscellaneous"))
        page.add(misc_group)

        transparency_row = Adw.ActionRow(
            title=_("Background Transparency"),
            subtitle=_("Adjust terminal background transparency"),
        )
        self.transparency_scale = Gtk.Scale.new_with_range(
            Gtk.Orientation.HORIZONTAL, 0, 100, 1
        )
        self.transparency_scale.set_value(self.settings_manager.get("transparency", 0))
        self.transparency_scale.set_draw_value(True)
        self.transparency_scale.set_value_pos(Gtk.PositionType.RIGHT)
        self.transparency_scale.set_hexpand(True)
        self.transparency_scale.connect("value-changed", self._on_transparency_changed)
        transparency_row.add_suffix(self.transparency_scale)
        transparency_row.set_activatable_widget(self.transparency_scale)
        misc_group.add(transparency_row)

        bold_bright_row = Adw.SwitchRow(
            title=_("Use Bright Colors for Bold Text"),
            subtitle=_("Render bold text with the brighter version of the base color"),
        )
        bold_bright_row.set_active(self.settings_manager.get("bold_is_bright", True))
        bold_bright_row.connect(
            "notify::active",
            lambda r, _: self._on_setting_changed("bold_is_bright", r.get_active()),
        )
        misc_group.add(bold_bright_row)

    def _setup_behavior_page(self) -> None:
        """Sets up the Terminal preferences page."""
        page = Adw.PreferencesPage(
            title=_("Behavior"), icon_name="preferences-system-symbolic"
        )
        self.add(page)

        startup_group = Adw.PreferencesGroup(title=_("Startup"))
        page.add(startup_group)

        restore_tabs_row = Adw.SwitchRow(
            title=_("Restore tabs on restart"),
            subtitle=_("Reopen tabs and panels from the previous session"),
        )
        restore_tabs_row.set_active(
            self.settings_manager.get("restore_tabs_on_restart", True)
        )
        restore_tabs_row.connect(
            "notify::active",
            lambda r, _: self._on_setting_changed(
                "restore_tabs_on_restart", r.get_active()
            ),
        )
        startup_group.add(restore_tabs_row)

        shell_group = Adw.PreferencesGroup(
            title=_("Shell"),
            description=_("Configure shell integration and behavior"),
        )
        page.add(shell_group)

        login_shell_row = Adw.SwitchRow(
            title=_("Run Command as a Login Shell"),
            subtitle=_("Sources /etc/profile and ~/.profile on startup"),
        )
        login_shell_row.set_active(self.settings_manager.get("use_login_shell", False))
        login_shell_row.connect(
            "notify::active",
            lambda r, _: self._on_setting_changed("use_login_shell", r.get_active()),
        )
        shell_group.add(login_shell_row)

        scrolling_group = Adw.PreferencesGroup(
            title=_("Scrolling"),
        )
        page.add(scrolling_group)

        scrollback_row = Adw.ActionRow(
            title=_("Scrollback Lines"),
            subtitle=_("Number of lines to keep in history (0 for unlimited)"),
        )
        scrollback_spin = Gtk.SpinButton.new_with_range(0, 1000000, 1000)
        scrollback_spin.set_value(self.settings_manager.get("scrollback_lines", 10000))
        scrollback_spin.connect("value-changed", self._on_scrollback_changed)
        scrollback_row.add_suffix(scrollback_spin)
        scrollback_row.set_activatable_widget(scrollback_spin)
        scrolling_group.add(scrollback_row)

        cursor_group = Adw.PreferencesGroup(title=_("Cursor"))
        page.add(cursor_group)

        cursor_shape_row = Adw.ComboRow(
            title=_("Cursor Shape"),
            subtitle=_("Select the shape of the terminal cursor"),
        )
        cursor_shape_row.set_model(
            Gtk.StringList.new([_("Block"), _("I-Beam"), _("Underline")])
        )
        cursor_shape_row.set_selected(self.settings_manager.get("cursor_shape", 0))
        cursor_shape_row.connect("notify::selected", self._on_cursor_shape_changed)
        cursor_group.add(cursor_shape_row)

        cursor_blink_row = Adw.ComboRow(
            title=_("Cursor Blinking"), subtitle=_("Control cursor blinking behavior")
        )
        cursor_blink_row.set_model(
            Gtk.StringList.new([_("Follow System"), _("On"), _("Off")])
        )
        cursor_blink_row.set_selected(self.settings_manager.get("cursor_blink", 0))
        cursor_blink_row.connect("notify::selected", self._on_cursor_blink_changed)
        cursor_group.add(cursor_blink_row)

        bell_group = Adw.PreferencesGroup(title=_("Bell"))
        page.add(bell_group)

        bell_row = Adw.SwitchRow(
            title=_("Audible Bell"),
            subtitle=_("Emit a sound for the terminal bell character"),
        )
        bell_row.set_active(self.settings_manager.get("bell_sound", False))
        bell_row.connect(
            "notify::active",
            lambda r, _: self._on_setting_changed("bell_sound", r.get_active()),
        )
        bell_group.add(bell_row)

        compatibility_group = Adw.PreferencesGroup(
            title=_("Compatibility"),
            description=_("Settings for compatibility with older systems and tools"),
        )
        page.add(compatibility_group)

        backspace_row = Adw.ComboRow(
            title=_("Backspace Key"), subtitle=_("Sequence to send for Backspace key")
        )
        backspace_row.set_model(
            Gtk.StringList.new([
                _("Automatic"),
                _("ASCII BACKSPACE (^H)"),
                _("ASCII DELETE"),
                _("Escape Sequence"),
            ])
        )
        backspace_row.set_selected(self.settings_manager.get("backspace_binding", 0))
        backspace_row.connect("notify::selected", self._on_backspace_binding_changed)
        compatibility_group.add(backspace_row)

        delete_row = Adw.ComboRow(
            title=_("Delete Key"), subtitle=_("Sequence to send for Delete key")
        )
        delete_row.set_model(
            Gtk.StringList.new([
                _("Automatic"),
                _("ASCII DELETE"),
                _("Escape Sequence"),
            ])
        )
        delete_row.set_selected(self.settings_manager.get("delete_binding", 0))
        delete_row.connect("notify::selected", self._on_delete_binding_changed)
        compatibility_group.add(delete_row)

        ambiguous_width_row = Adw.ComboRow(
            title=_("Ambiguous-width Characters"),
            subtitle=_("Render ambiguous characters as narrow or wide"),
        )
        ambiguous_width_row.set_model(Gtk.StringList.new([_("Narrow"), _("Wide")]))
        ambiguous_width_row.set_selected(
            self.settings_manager.get("cjk_ambiguous_width", 1) - 1
        )
        ambiguous_width_row.connect(
            "notify::selected", self._on_ambiguous_width_changed
        )
        compatibility_group.add(ambiguous_width_row)

    def _setup_shortcuts_page(self) -> None:
        shortcuts_page = Adw.PreferencesPage(
            title=_("Shortcuts"),
            icon_name="preferences-desktop-keyboard-shortcuts-symbolic",
        )
        self.add(shortcuts_page)
        terminal_group = Adw.PreferencesGroup(
            title=_("Terminal Actions"),
            description=_("Keyboard shortcuts for terminal operations"),
        )
        shortcuts_page.add(terminal_group)
        app_group = Adw.PreferencesGroup(
            title=_("Application Actions"),
            description=_("Keyboard shortcuts for application operations"),
        )
        shortcuts_page.add(app_group)
        terminal_shortcuts = {
            "new-local-tab": _("New Tab"),
            "close-tab": _("Close Tab"),
            "copy": _("Copy"),
            "paste": _("Paste"),
            "select-all": _("Select All"),
        }
        app_shortcuts = {
            "preferences": _("Preferences"),
            "quit": _("Quit Application"),
            "toggle-sidebar": _("Toggle Sidebar"),
            "new-window": _("New Window"),
        }
        self._create_shortcut_rows(terminal_group, terminal_shortcuts)
        self._create_shortcut_rows(app_group, app_shortcuts)

    def _create_shortcut_rows(
        self, group: Adw.PreferencesGroup, shortcuts: Dict[str, str]
    ) -> None:
        for key, title in shortcuts.items():
            current_shortcut = self.settings_manager.get_shortcut(key)
            subtitle = (
                accelerator_to_label(current_shortcut)
                if current_shortcut
                else _("None")
            )
            row = Adw.ActionRow(title=title, subtitle=subtitle)
            button = Gtk.Button(label=_("Edit"), css_classes=["flat"])
            button.connect("clicked", self._on_shortcut_edit_clicked, key, row)
            row.add_suffix(button)
            row.set_activatable_widget(button)
            group.add(row)
            self.shortcut_rows[key] = row

    def _setup_advanced_page(self) -> None:
        advanced_page = Adw.PreferencesPage(
            title=_("Advanced"), icon_name="preferences-other-symbolic"
        )
        self.add(advanced_page)

        features_group = Adw.PreferencesGroup(
            title=_("Advanced Features"),
            description=_("Enable or disable advanced terminal features"),
        )
        advanced_page.add(features_group)

        bidi_row = Adw.SwitchRow(
            title=_("Bidirectional Text Support"),
            subtitle=_(
                "Enable for languages like Arabic and Hebrew (may affect performance)"
            ),
        )
        bidi_row.set_active(self.settings_manager.get("bidi_enabled", False))
        bidi_row.connect(
            "notify::active",
            lambda r, _: self._on_setting_changed("bidi_enabled", r.get_active()),
        )
        features_group.add(bidi_row)

        sixel_row = Adw.SwitchRow(
            title=_("SIXEL Graphics Support"),
            subtitle=_("Allow the terminal to display SIXEL images (experimental)"),
        )
        sixel_row.set_active(self.settings_manager.get("sixel_enabled", True))
        sixel_row.connect(
            "notify::active",
            lambda r, _: self._on_setting_changed("sixel_enabled", r.get_active()),
        )
        features_group.add(sixel_row)

        accessibility_group = Adw.PreferencesGroup(
            title=_("Accessibility"),
            description=_(
                "Settings for screen readers and other assistive technologies"
            ),
        )
        advanced_page.add(accessibility_group)

        a11y_row = Adw.SwitchRow(
            title=_("Allow Screen Readers"),
            subtitle=_("Permit tools like Orca to read terminal content"),
        )
        a11y_row.set_active(self.settings_manager.get("accessibility_enabled", True))
        a11y_row.connect(
            "notify::active",
            lambda r, _: self._on_setting_changed(
                "accessibility_enabled", r.get_active()
            ),
        )
        accessibility_group.add(a11y_row)

        backup_group = Adw.PreferencesGroup(
            title=_("Backup & Recovery"),
            description=_("Configure automatic backup and recovery options"),
        )
        advanced_page.add(backup_group)
        auto_backup_row = Adw.SwitchRow(
            title=_("Automatic Backups"),
            subtitle=_("Automatically create backups of sessions and settings"),
        )
        auto_backup_row.set_active(
            self.settings_manager.get("auto_backup_enabled", False)
        )
        auto_backup_row.connect(
            "notify::active",
            lambda r, _: self._on_setting_changed(
                "auto_backup_enabled", r.get_active()
            ),
        )
        backup_group.add(auto_backup_row)

        log_group = Adw.PreferencesGroup(
            title=_("Logging"), description=_("Configure application logging behavior")
        )
        advanced_page.add(log_group)

        log_to_file_row = Adw.SwitchRow(
            title=_("Save Logs to File"),
            subtitle=_("Save application logs to the configuration directory"),
        )
        log_to_file_row.set_active(self.settings_manager.get("log_to_file", False))
        log_to_file_row.connect(
            "notify::active",
            lambda r, _: self._on_setting_changed("log_to_file", r.get_active()),
        )
        log_group.add(log_to_file_row)

        log_level_row = Adw.ComboRow(
            title=_("Console Log Level"),
            subtitle=_("Set the minimum level of messages shown in the console"),
        )
        log_levels = ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]
        log_level_row.set_model(Gtk.StringList.new(log_levels))
        current_level = self.settings_manager.get("console_log_level", "ERROR")
        try:
            selected_index = log_levels.index(current_level.upper())
        except ValueError:
            selected_index = 3
        log_level_row.set_selected(selected_index)
        log_level_row.connect("notify::selected", self._on_log_level_changed)
        log_group.add(log_level_row)

        reset_group = Adw.PreferencesGroup(
            title=_("Reset"), description=_("Reset application settings to defaults")
        )
        advanced_page.add(reset_group)
        reset_row = Adw.ActionRow(
            title=_("Reset All Settings"),
            subtitle=_("Restore all settings to their default values"),
        )
        reset_button = Gtk.Button(label=_("Reset"), css_classes=["destructive-action"])
        reset_button.connect("clicked", self._on_reset_settings_clicked)
        reset_row.add_suffix(reset_button)
        reset_row.set_activatable_widget(reset_button)
        reset_group.add(reset_row)

    def _on_log_level_changed(self, combo_row, _param):
        selected_item = combo_row.get_selected_item()
        if selected_item:
            level_str = selected_item.get_string()
            self._on_setting_changed("console_log_level", level_str)

    def _on_palette_selected(self, button, index):
        if button.get_active():
            self._on_color_scheme_changed(None, index)

    def _on_color_scheme_changed(self, combo_row, index) -> None:
        self.settings_manager.set("color_scheme", index)
        self.emit("color-scheme-changed", index)

    def _on_transparency_changed(self, scale) -> None:
        value = scale.get_value()
        self.settings_manager.set("transparency", value)
        self.emit("transparency-changed", value)

    def _on_font_changed(self, font_button) -> None:
        font = font_button.get_font()
        self.settings_manager.set("font", font)
        self.emit("font-changed", font)

    def _on_setting_changed(self, key: str, value: Any) -> None:
        self.settings_manager.set(key, value)
        self.emit("setting-changed", key, value)

    def _on_instance_behavior_changed(self, combo_row, _param) -> None:
        value = "new_window" if combo_row.get_selected() == 1 else "new_tab"
        self._on_setting_changed("new_instance_behavior", value)

    def _on_scrollback_changed(self, spin_button) -> None:
        value = int(spin_button.get_value())
        self._on_setting_changed("scrollback_lines", value)

    def _on_cursor_shape_changed(self, combo_row, _param) -> None:
        index = combo_row.get_selected()
        self._on_setting_changed("cursor_shape", index)

    def _on_cursor_blink_changed(self, combo_row, _param) -> None:
        index = combo_row.get_selected()
        self._on_setting_changed("cursor_blink", index)

    def _on_text_blink_mode_changed(self, combo_row, _param) -> None:
        index = combo_row.get_selected()
        self._on_setting_changed("text_blink_mode", index)

    def _on_line_spacing_changed(self, spin_button) -> None:
        value = spin_button.get_value()
        self._on_setting_changed("line_spacing", value)

    def _on_backspace_binding_changed(self, combo_row, _param) -> None:
        index = combo_row.get_selected()
        self._on_setting_changed("backspace_binding", index)

    def _on_delete_binding_changed(self, combo_row, _param) -> None:
        index = combo_row.get_selected()
        self._on_setting_changed("delete_binding", index)

    def _on_ambiguous_width_changed(self, combo_row, _param) -> None:
        # VTE usa 1 para Narrow e 2 para Wide, o combo usa 0 e 1.
        value = combo_row.get_selected() + 1
        self._on_setting_changed("cjk_ambiguous_width", value)

    def _on_shortcut_edit_clicked(
        self, button, shortcut_key: str, row: Adw.ActionRow
    ) -> None:
        dialog = Adw.MessageDialog(
            transient_for=self,
            title=_("Edit Shortcut"),
            body=_("Press the new key combination for '{}' or Esc to cancel.").format(
                row.get_title()
            ),
        )
        current_shortcut = self.settings_manager.get_shortcut(shortcut_key)
        current_label = (
            accelerator_to_label(current_shortcut) if current_shortcut else _("None")
        )
        feedback_label = Gtk.Label(
            label=_("Current: {}\nNew: (press keys)").format(current_label)
        )
        dialog.set_extra_child(feedback_label)
        key_controller = Gtk.EventControllerKey()
        new_shortcut = [None]

        def on_key_pressed(_controller, keyval, _keycode, state):
            if keyval in (
                Gdk.KEY_Control_L,
                Gdk.KEY_Control_R,
                Gdk.KEY_Shift_L,
                Gdk.KEY_Shift_R,
                Gdk.KEY_Alt_L,
                Gdk.KEY_Alt_R,
                Gdk.KEY_Super_L,
                Gdk.KEY_Super_R,
            ):
                return Gdk.EVENT_PROPAGATE
            if keyval == Gdk.KEY_Escape:
                new_shortcut[0] = "cancel"
                dialog.response("cancel")
                return Gdk.EVENT_STOP
            shortcut_string = Gtk.accelerator_name(
                keyval, state & Gtk.accelerator_get_default_mod_mask()
            )
            new_shortcut[0] = shortcut_string
            label_text = accelerator_to_label(shortcut_string)
            feedback_label.set_label(
                _("Current: {}\nNew: {}").format(current_label, label_text)
            )
            return Gdk.EVENT_STOP

        key_controller.connect("key-pressed", on_key_pressed)
        dialog.add_controller(key_controller)
        dialog.add_response("cancel", _("Cancel"))
        dialog.add_response("clear", _("Clear"))
        dialog.add_response("save", _("Set Shortcut"))
        dialog.set_default_response("save")
        dialog.set_response_appearance("save", Adw.ResponseAppearance.SUGGESTED)
        dialog.set_response_appearance("clear", Adw.ResponseAppearance.DESTRUCTIVE)

        def on_response(dlg, response_id):
            if (
                response_id == "save"
                and new_shortcut[0]
                and new_shortcut[0] != "cancel"
            ):
                self.settings_manager.set_shortcut(shortcut_key, new_shortcut[0])
                row.set_subtitle(accelerator_to_label(new_shortcut[0]))
                self.emit("shortcut-changed")
            elif response_id == "clear":
                self.settings_manager.set_shortcut(shortcut_key, "")
                row.set_subtitle(_("None"))
                self.emit("shortcut-changed")
            dlg.close()

        dialog.connect("response", on_response)
        dialog.present()

    def _on_reset_settings_clicked(self, button) -> None:
        dialog = Adw.MessageDialog(
            transient_for=self,
            title=_("Reset All Settings"),
            body=_(
                "Are you sure you want to reset all settings to their default values? This action cannot be undone."
            ),
        )
        dialog.add_response("cancel", _("Cancel"))
        dialog.add_response("reset", _("Reset All Settings"))
        dialog.set_response_appearance("reset", Adw.ResponseAppearance.DESTRUCTIVE)

        def on_response(dlg, response_id):
            if response_id == "reset":
                try:
                    self.settings_manager.reset_to_defaults()
                    success_dialog = Adw.MessageDialog(
                        transient_for=self,
                        title=_("Settings Reset"),
                        body=_(
                            "All settings have been reset to their default values. Please restart the application for all changes to take effect."
                        ),
                    )
                    success_dialog.add_response("ok", _("OK"))
                    success_dialog.present()
                    self.logger.info("All settings reset to defaults")
                except Exception as e:
                    self.logger.error(f"Failed to reset settings: {e}")
                    error_dialog = Adw.MessageDialog(
                        transient_for=self,
                        title=_("Reset Failed"),
                        body=_("Failed to reset settings: {}").format(e),
                    )
                    error_dialog.add_response("ok", _("OK"))
                    error_dialog.present()
            dlg.close()

        dialog.connect("response", on_response)
        dialog.present()


class MoveSessionDialog(BaseDialog):
    """A dialog to move a session to a different folder."""

    def __init__(
        self,
        parent_window,
        session_to_move: SessionItem,
        folder_store: Gio.ListStore,
        operations: SessionOperations,
    ):
        title = _("Move Session")
        super().__init__(parent_window, title, default_width=400, default_height=250)
        self.session_to_move = session_to_move
        self.folder_store = folder_store
        self.operations = operations
        self.folder_paths_map: Dict[str, str] = {}
        self._setup_ui()
        self.logger.info(f"Move session dialog opened for '{session_to_move.name}'")

    def _setup_ui(self):
        main_box = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=16,
            margin_top=24,
            margin_bottom=24,
            margin_start=24,
            margin_end=24,
        )
        group = Adw.PreferencesGroup(
            title=_("Select Destination"),
            description=_("Choose the folder to move the session '{name}' to.").format(
                name=self.session_to_move.name
            ),
        )
        main_box.append(group)
        folder_row = Adw.ComboRow(
            title=_("Destination Folder"),
            subtitle=_("Select a folder or 'Root' for the top level"),
        )
        self.folder_combo = folder_row
        group.add(folder_row)
        self._populate_folder_combo()
        action_bar = Gtk.ActionBar()
        cancel_button = Gtk.Button(label=_("Cancel"))
        cancel_button.connect("clicked", self._on_cancel_clicked)
        action_bar.pack_start(cancel_button)
        move_button = Gtk.Button(label=_("Move"), css_classes=["suggested-action"])
        move_button.connect("clicked", self._on_move_clicked)
        action_bar.pack_end(move_button)
        self.set_default_widget(move_button)
        content_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        content_box.append(main_box)
        content_box.append(action_bar)
        self.set_content(content_box)

    def _populate_folder_combo(self):
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
        selected_index = 0
        current_path = self.session_to_move.folder_path

        for i, folder in enumerate(folders):
            display_name = f"{'  ' * folder.path.count('/')}{folder.name}"
            folder_model.append(display_name)
            self.folder_paths_map[display_name] = folder.path
            if folder.path == current_path:
                selected_index = i + 1

        self.folder_combo.set_model(folder_model)
        self.folder_combo.set_selected(selected_index)

    def _on_move_clicked(self, button):
        selected_item = self.folder_combo.get_selected_item()
        if not selected_item:
            return
        display_name = selected_item.get_string()
        target_folder_path = self.folder_paths_map.get(display_name, "")
        if (
            self.session_to_move
            and target_folder_path == self.session_to_move.folder_path
        ):
            self.close()
            return
        result = self.operations.move_session_to_folder(
            self.session_to_move, target_folder_path
        )
        if result.success:
            if (
                result.warnings
                and hasattr(self.parent_window, "get_toast_overlay")
                and (overlay := self.parent_window.get_toast_overlay())
            ):
                overlay.add_toast(Adw.Toast(title=result.warnings[0]))
            self.logger.info(
                f"Session '{self.session_to_move.name}' moved to '{target_folder_path}'"
            )
            self.parent_window.refresh_tree()
            self.close()
        else:
            self._show_error_dialog(_("Move Failed"), result.message)
