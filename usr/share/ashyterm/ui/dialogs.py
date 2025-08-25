"""
Enhanced UI dialogs for Ashy Terminal.

This module provides comprehensive dialog components with validation, security,
platform compatibility, and integrated error handling for session management
and application preferences.
"""

import os
import threading
from ..terminal.spawner import get_spawner
import time
from typing import Optional, Dict, Any, List, Callable
from pathlib import Path
from ..sessions.operations import SessionOperations
import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Gtk, Adw, Gio, GLib, Gdk, GObject

# Import models and storage
from ..sessions.models import SessionItem, SessionFolder
from ..sessions.storage import save_sessions_and_folders

# Import settings and configuration
from ..settings.config import (
    AppConstants, DefaultSettings, ColorSchemes, ColorSchemeMap,
    get_config_paths, NetworkConstants, SecurityConstants
)
from ..settings.manager import SettingsManager

# Import new utility systems
from ..utils.logger import get_logger, log_session_event, log_error_with_context
from ..utils.exceptions import (
    DialogError, ValidationError, SessionValidationError,
    SSHKeyError, HostnameValidationError, PathValidationError,
    handle_exception, ErrorCategory, ErrorSeverity, AshyTerminalError
)
from ..utils.security import (
    validate_ssh_hostname, validate_ssh_key_file, validate_file_path,
    SSHKeyValidator, HostnameValidator, InputSanitizer,
    validate_session_data
)
from ..utils.platform import (
    get_platform_info, get_ssh_directory, normalize_path,
    get_path_manager, is_windows
)
from ..utils.backup import get_backup_manager, BackupType
from ..utils.crypto import is_encryption_available, get_secure_storage
from ..helpers import accelerator_to_label
from ..utils.translation_utils import _


class BaseDialog(Adw.Window):
    """
    Base dialog class with enhanced functionality and error handling.
    
    Provides common functionality for all dialogs including validation,
    error handling, security checks, and platform compatibility.
    """
    
    def __init__(self, parent_window, dialog_title: str, **kwargs):
        """
        Initialize base dialog.
        
        Args:
            parent_window: Parent window
            dialog_title: Dialog title
            **kwargs: Additional window properties
        """
        # Set default properties
        default_props = {
            'title': dialog_title,
            'modal': True,
            'transient_for': parent_window,
            'hide_on_close': True,
            'default_width': 450,
            'default_height': 500
        }
        default_props.update(kwargs)
        
        super().__init__(**default_props)
        
        # Initialize logging and utilities
        self.logger = get_logger(f'ashyterm.ui.dialogs.{self.__class__.__name__.lower()}')
        self.parent_window = parent_window
        self.platform_info = get_platform_info()
        self.config_paths = get_config_paths()
        
        # Security auditor removed
        self.security_auditor = None
        
        # Validation state
        self._validation_errors: List[str] = []
        self._is_validating = False
        
        # Thread safety
        self._ui_lock = threading.Lock()
        
        # Track changes for unsaved warning
        self._has_changes = False
        self._original_data: Optional[Dict[str, Any]] = None
        
        self.logger.debug(f"Dialog initialized: {dialog_title}")

        # Triggers the same logic as the cancel button.
        key_controller = Gtk.EventControllerKey()
        key_controller.connect("key-pressed", self._on_key_pressed)
        self.add_controller(key_controller)
    
    def _on_key_pressed(self, controller, keyval, keycode, state):
        """Handle key presses for the dialog (e.g., Escape to cancel)."""
        if keyval == Gdk.KEY_Escape:
            # Triggers the same logic as the cancel button.
            self._on_cancel_clicked(None)
            return Gdk.EVENT_STOP
        return Gdk.EVENT_PROPAGATE

    def _on_cancel_clicked(self, button):
        """Default cancel action. Subclasses can override this."""
        self.close()

    def _mark_changed(self):
        """Mark dialog as having unsaved changes."""
        self._has_changes = True
    
    def _show_error_dialog(self, title: str, message: str, details: Optional[str] = None) -> None:
        """
        Show error dialog with enhanced error information.
        
        Args:
            title: Error title
            message: Primary error message
            details: Optional detailed error information
        """
        try:
            dialog = Adw.MessageDialog(
                transient_for=self,
                title=title,
                body=message
            )
            
            # Add details if provided
            if details:
                dialog.set_body_use_markup(True)
                full_body = f"{message}\n\n<small>{GLib.markup_escape_text(details)}</small>"
                dialog.set_body(full_body)
            
            dialog.add_response("ok", _("OK"))
            dialog.present()
            
            self.logger.warning(f"Error dialog shown: {title} - {message}")
            
        except Exception as e:
            self.logger.error(f"Failed to show error dialog: {e}")
            # Fallback to basic message
            print(f"ERROR: {title} - {message}")
    
    def _show_warning_dialog(self, title: str, message: str, 
                           on_confirm: Optional[Callable] = None) -> None:
        """
        Show warning dialog with confirmation.
        
        Args:
            title: Warning title
            message: Warning message
            on_confirm: Callback for confirmation
        """
        try:
            dialog = Adw.MessageDialog(
                transient_for=self,
                title=title,
                body=message
            )
            
            dialog.add_response("cancel", _("Cancel"))
            dialog.add_response("confirm", _("Continue"))
            dialog.set_response_appearance("confirm", Adw.ResponseAppearance.DESTRUCTIVE)
            
            def on_response(dlg, response_id):
                if response_id == "confirm" and on_confirm:
                    on_confirm()
                dlg.close()
            
            dialog.connect("response", on_response)
            dialog.present()
            
        except Exception as e:
            self.logger.error(f"Failed to show warning dialog: {e}")
    
    def _validate_required_field(self, entry: Gtk.Entry, field_name: str) -> bool:
        """
        Validate required field with visual feedback.
        
        Args:
            entry: Entry widget to validate
            field_name: Human-readable field name
            
        Returns:
            True if field is valid
        """
        value = entry.get_text().strip()
        if not value:
            entry.add_css_class("error")
            self._validation_errors.append(_("{} is required").format(field_name))
            return False
        else:
            entry.remove_css_class("error")
            return True
    
    def _clear_validation_errors(self):
        """Clear validation errors."""
        self._validation_errors.clear()
    
    def _has_validation_errors(self) -> bool:
        """Check if there are validation errors."""
        return len(self._validation_errors) > 0


class SessionEditDialog(BaseDialog):
    """Enhanced dialog for creating and editing sessions with comprehensive validation."""
    
    def __init__(self, parent_window, session_item: SessionItem, session_store, 
                 position: int, folder_store=None):
        """
        Initialize session edit dialog.
        
        Args:
            parent_window: Parent window
            session_item: SessionItem to edit (new if position == -1)
            session_store: Store containing sessions
            position: Position in store (-1 for new)
            folder_store: Store containing folders
        """
        self.is_new_item = (position == -1)
        title = _("Add Session") if self.is_new_item else _("Edit Session")
        
        super().__init__(
            parent_window, 
            title,
            default_width=860,
            default_height=680
        )
        
        # Session management
        self.session_store = session_store
        self.folder_store = folder_store
        self.position = position
        
        # Create working copy for editing
        self.editing_session = SessionItem.from_dict(session_item.to_dict()) if not self.is_new_item else session_item
        self.original_session = session_item if not self.is_new_item else None
        self._original_data = self.editing_session.to_dict()
        
        # UI components
        self.name_entry: Optional[Gtk.Entry] = None
        self.folder_combo: Optional[Gtk.DropDown] = None
        self.type_combo: Optional[Gtk.DropDown] = None
        self.host_entry: Optional[Gtk.Entry] = None
        self.user_entry: Optional[Gtk.Entry] = None
        self.port_entry: Optional[Gtk.SpinButton] = None
        self.auth_combo: Optional[Gtk.DropDown] = None
        self.key_path_entry: Optional[Gtk.Entry] = None
        self.password_entry: Optional[Gtk.PasswordEntry] = None
        self.browse_button: Optional[Gtk.Button] = None
        
        # UI containers
        self.ssh_box: Optional[Gtk.Box] = None
        self.key_box: Optional[Gtk.Box] = None
        self.password_box: Optional[Gtk.Box] = None
        
        # Folder mapping
        self.folder_paths_map: Dict[str, str] = {}
        
        # Initialize UI
        self._setup_ui()

        # Connect to map signal to set focus after the dialog is shown
        self.connect("map", self._on_map)

        self.logger.info(
            f"Session edit dialog opened: {self.editing_session.name} ({'new' if self.is_new_item else 'edit'})"
        )

    def _on_map(self, widget):
        """Set focus when the dialog is mapped."""
        if self.name_entry:
            self.name_entry.grab_focus()

    def _setup_ui(self) -> None:
        """Set up the complete dialog user interface."""
        try:
            main_box = Gtk.Box(
                orientation=Gtk.Orientation.VERTICAL,
                spacing=16,
                margin_top=24,
                margin_bottom=24,
                margin_start=24,
                margin_end=24,
            )

            # Create all sections
            self._create_name_section(main_box)

            if self.folder_store:
                self._create_folder_section(main_box)

            self._create_type_section(main_box)
            self._create_ssh_section(main_box)

            # Action bar
            action_bar = self._create_action_bar()

            # Layout with scrolling
            content_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
            scrolled_window = Gtk.ScrolledWindow(vexpand=True, hexpand=True)
            scrolled_window.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
            scrolled_window.set_child(main_box)

            content_box.append(scrolled_window)
            content_box.append(action_bar)

            self.set_content(content_box)

            # Set initial visibility
            self._update_ssh_visibility()
            self._update_auth_visibility()

            self.logger.debug("Session edit dialog UI setup completed")

        except Exception as e:
            self.logger.error(f"Failed to setup UI: {e}")
            self._show_error_dialog(
                _("UI Error"), _("Failed to initialize dialog interface")
            )
            self.close()

    def _create_name_section(self, parent: Gtk.Box) -> None:
        """Create session name input section with validation."""
        try:
            name_group = Adw.PreferencesGroup(title=_("Session Information"))

            # Session name row
            name_row = Adw.ActionRow(
                title=_("Session Name"),
                subtitle=_("A descriptive name for this session"),
            )

            self.name_entry = Gtk.Entry(
                text=self.editing_session.name,
                placeholder_text=_("Enter session name..."),
                hexpand=True,
            )
            self.name_entry.connect("changed", self._on_name_changed)
            # Connect the Enter key to save
            self.name_entry.connect("activate", self._on_save_clicked)

            name_row.add_suffix(self.name_entry)
            name_row.set_activatable_widget(self.name_entry)
            name_group.add(name_row)

            parent.append(name_group)

        except Exception as e:
            self.logger.error(f"Failed to create name section: {e}")
            raise DialogError("name_section", str(e))

    def _create_folder_section(self, parent: Gtk.Box) -> None:
        """Create folder selection section."""
        try:
            folder_group = Adw.PreferencesGroup(title=_("Organization"))

            # Folder selection row
            folder_row = Adw.ComboRow(
                title=_("Folder"),
                subtitle=_("Choose a folder to organize this session"),
            )

            # Build folder model
            folder_model = Gtk.StringList()
            folder_model.append(_("Root"))
            self.folder_paths_map = {_("Root"): ""}

            # Add folders sorted by path
            folders = []
            for i in range(self.folder_store.get_n_items()):
                folder = self.folder_store.get_item(i)
                if isinstance(folder, SessionFolder):
                    folders.append(folder)

            sorted_folders = sorted(folders, key=lambda f: f.path)
            for folder in sorted_folders:
                # Create indented display name
                depth = folder.path.count("/")
                display_name = f"{'  ' * depth}{folder.name}"
                folder_model.append(display_name)
                self.folder_paths_map[display_name] = folder.path

            folder_row.set_model(folder_model)

            # Set current selection
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

        except Exception as e:
            self.logger.error(f"Failed to create folder section: {e}")
            raise DialogError("folder_section", str(e))

    def _create_type_section(self, parent: Gtk.Box) -> None:
        """Create session type selection section."""
        try:
            type_group = Adw.PreferencesGroup(title=_("Connection Type"))

            # Session type row
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

        except Exception as e:
            self.logger.error(f"Failed to create type section: {e}")
            raise DialogError("type_section", str(e))

    def _create_ssh_section(self, parent: Gtk.Box) -> None:
        """Create SSH configuration section with comprehensive validation."""
        try:
            # SSH configuration group
            ssh_group = Adw.PreferencesGroup(
                title=_("SSH Configuration"),
                description=_("Configure connection details for SSH sessions"),
            )

            # Host row
            host_row = Adw.ActionRow(
                title=_("Host"),
                subtitle=_("Hostname or IP address of the remote server"),
            )

            self.host_entry = Gtk.Entry(
                text=self.editing_session.host,
                placeholder_text=_("example.com or 192.168.1.100"),
                hexpand=True,
            )
            self.host_entry.connect("changed", self._on_host_changed)
            # Connect the Enter key to save
            self.host_entry.connect("activate", self._on_save_clicked)

            host_row.add_suffix(self.host_entry)
            host_row.set_activatable_widget(self.host_entry)
            ssh_group.add(host_row)

            # Username row
            user_row = Adw.ActionRow(
                title=_("Username"),
                subtitle=_("Username for SSH authentication (optional)"),
            )

            self.user_entry = Gtk.Entry(
                text=self.editing_session.user,
                placeholder_text=_("username"),
                hexpand=True,
            )
            self.user_entry.connect("changed", self._on_user_changed)
            # Connect the Enter key to save
            self.user_entry.connect("activate", self._on_save_clicked)

            user_row.add_suffix(self.user_entry)
            user_row.set_activatable_widget(self.user_entry)
            ssh_group.add(user_row)

            # Port row
            port_row = Adw.ActionRow(
                title=_("Port"), subtitle=_("SSH port number (default: 22)")
            )

            self.port_entry = Gtk.SpinButton.new_with_range(1, 65535, 1)
            self.port_entry.set_value(self.editing_session.port)
            self.port_entry.connect("value-changed", self._on_port_changed)

            port_row.add_suffix(self.port_entry)
            port_row.set_activatable_widget(self.port_entry)
            ssh_group.add(port_row)

            # Authentication type row
            auth_row = Adw.ComboRow(
                title=_("Authentication"), subtitle=_("Choose authentication method")
            )

            auth_model = Gtk.StringList.new([_("SSH Key"), _("Password")])
            auth_row.set_model(auth_model)
            auth_row.set_selected(0 if self.editing_session.uses_key_auth() else 1)
            auth_row.connect("notify::selected", self._on_auth_changed)

            self.auth_combo = auth_row
            ssh_group.add(auth_row)

            # SSH key section
            self._create_ssh_key_section(ssh_group)

            # Password section
            self._create_password_section(ssh_group)

            # Store SSH container
            self.ssh_box = ssh_group
            parent.append(ssh_group)

        except Exception as e:
            self.logger.error(f"Failed to create SSH section: {e}")
            raise DialogError("ssh_section", str(e))

    def _create_ssh_key_section(self, parent: Adw.PreferencesGroup) -> None:
        """Create SSH key configuration section."""
        try:
            # SSH key row
            key_row = Adw.ActionRow(
                title=_("SSH Key Path"), subtitle=_("Path to private SSH key file")
            )

            # Key path entry with browse button
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
            # Connect the Enter key to save
            self.key_path_entry.connect("activate", self._on_save_clicked)

            self.browse_button = Gtk.Button(label=_("Browse..."), css_classes=["flat"])
            self.browse_button.connect("clicked", self._on_browse_key_clicked)

            key_box.append(self.key_path_entry)
            key_box.append(self.browse_button)

            key_row.add_suffix(key_box)
            key_row.set_activatable_widget(self.key_path_entry)

            self.key_box = key_row
            parent.add(key_row)

        except Exception as e:
            self.logger.error(f"Failed to create SSH key section: {e}")
            raise DialogError("ssh_key_section", str(e))

    def _create_password_section(self, parent: Adw.PreferencesGroup) -> None:
        """Create password authentication section."""
        try:
            # Password row
            password_row = Adw.ActionRow(
                title=_("Password"), subtitle=_("Password for SSH authentication")
            )

            # Show encryption status
            if is_encryption_available():
                try:
                    storage = get_secure_storage()
                    if storage.is_initialized():
                        password_row.set_subtitle(
                            _("Password for SSH authentication (encrypted storage)")
                        )
                    else:
                        password_row.set_subtitle(
                            _(
                                "Password for SSH authentication (encryption available but not initialized)"
                            )
                        )
                except Exception:
                    password_row.set_subtitle(
                        _("Password for SSH authentication (encryption not available)")
                    )
            else:
                password_row.set_subtitle(
                    _("Password for SSH authentication (stored as plain text)")
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
            # Connect the Enter key to save
            self.password_entry.connect("activate", self._on_save_clicked)

            password_row.add_suffix(self.password_entry)
            password_row.set_activatable_widget(self.password_entry)

            self.password_box = password_row
            parent.add(password_row)

        except Exception as e:
            self.logger.error(f"Failed to create password section: {e}")
            raise DialogError("password_section", str(e))

    def _create_action_bar(self) -> Gtk.ActionBar:
        """Create action bar with enhanced buttons."""
        try:
            action_bar = Gtk.ActionBar()

            # Cancel button
            cancel_button = Gtk.Button(label=_("Cancel"))
            cancel_button.connect("clicked", self._on_cancel_clicked)
            action_bar.pack_start(cancel_button)

            # Test connection button (for SSH)
            self.test_button = Gtk.Button(
                label=_("Test Connection"), css_classes=["flat"]
            )
            self.test_button.connect("clicked", self._on_test_connection_clicked)
            action_bar.pack_start(self.test_button)

            # Save button
            save_button = Gtk.Button(label=_("Save"), css_classes=["suggested-action"])
            save_button.connect("clicked", self._on_save_clicked)
            action_bar.pack_end(save_button)

            # Sets the Save button as the default widget for the Enter key
            self.set_default_widget(save_button)

            return action_bar

        except Exception as e:
            self.logger.error(f"Failed to create action bar: {e}")
            raise DialogError("action_bar", str(e))

    # Event handlers
    def _on_name_changed(self, entry: Gtk.Entry) -> None:
        """Handle session name change with validation."""
        try:
            entry.remove_css_class("error")
            self._mark_changed()
        except Exception as e:
            self.logger.error(f"Name change handling failed: {e}")

    def _on_folder_changed(self, combo_row, param) -> None:
        """Handle folder selection change."""
        try:
            self._mark_changed()
        except Exception as e:
            self.logger.error(f"Folder change handling failed: {e}")

    def _on_type_changed(self, combo_row, param) -> None:
        """Handle session type change."""
        try:
            # If changed to SSH and it's a new session, set default key path
            if combo_row.get_selected() == 1:  # SSH selected
                if self.is_new_item and self.auth_combo.get_selected() == 0:  # Key auth
                    if not self.key_path_entry.get_text().strip():
                        self.key_path_entry.set_text(f"{get_ssh_directory()}/id_rsa")

            self._update_ssh_visibility()
            self._mark_changed()
        except Exception as e:
            self.logger.error(f"Type change handling failed: {e}")

    def _on_host_changed(self, entry: Gtk.Entry) -> None:
        """Handle host change with validation."""
        try:
            entry.remove_css_class("error")
            self._mark_changed()

            # Real-time hostname validation
            hostname = entry.get_text().strip()
            if hostname and not HostnameValidator.is_valid_hostname(hostname):
                entry.add_css_class("error")

        except Exception as e:
            self.logger.error(f"Host change handling failed: {e}")

    def _on_user_changed(self, entry: Gtk.Entry) -> None:
        """Handle username change."""
        try:
            self._mark_changed()
        except Exception as e:
            self.logger.error(f"User change handling failed: {e}")

    def _on_port_changed(self, spin_button: Gtk.SpinButton) -> None:
        """Handle port change with validation."""
        try:
            self._mark_changed()

            # Real-time port validation
            port = int(spin_button.get_value())
            if not (1 <= port <= 65535):
                spin_button.add_css_class("error")
            else:
                spin_button.remove_css_class("error")

        except Exception as e:
            self.logger.error(f"Port change handling failed: {e}")

    def _on_auth_changed(self, combo_row, param) -> None:
        """Handle authentication type change."""
        try:
            # If changed to key auth and field is empty, fill with default
            if combo_row.get_selected() == 0:  # SSH Key selected
                current_value = self.key_path_entry.get_text().strip()
                if not current_value:
                    self.key_path_entry.set_text(f"{get_ssh_directory()}/id_rsa")

            self._update_auth_visibility()
            self._mark_changed()
        except Exception as e:
            self.logger.error(f"Auth change handling failed: {e}")

    def _on_key_path_changed(self, entry: Gtk.Entry) -> None:
        """Handle SSH key path change with validation."""
        try:
            entry.remove_css_class("error")
            self._mark_changed()

            # Real-time key validation
            key_path = entry.get_text().strip()
            if key_path:
                try:
                    validate_ssh_key_file(key_path)
                except Exception:
                    entry.add_css_class("error")

        except Exception as e:
            self.logger.error(f"Key path change handling failed: {e}")

    def _on_password_changed(self, entry: Gtk.PasswordEntry) -> None:
        """Handle password change."""
        try:
            self._mark_changed()
        except Exception as e:
            self.logger.error(f"Password change handling failed: {e}")

    def _update_ssh_visibility(self) -> None:
        """Update SSH section visibility based on session type."""
        try:
            if self.ssh_box and self.type_combo:
                is_ssh = self.type_combo.get_selected() == 1
                self.ssh_box.set_visible(is_ssh)

                # Update test button visibility
                if hasattr(self, "test_button"):
                    self.test_button.set_visible(is_ssh)

        except Exception as e:
            self.logger.error(f"Failed to update SSH visibility: {e}")

    def _update_auth_visibility(self) -> None:
        """Update authentication section visibility based on auth type."""
        try:
            if self.key_box and self.password_box and self.auth_combo:
                is_key = self.auth_combo.get_selected() == 0
                self.key_box.set_visible(is_key)
                self.password_box.set_visible(not is_key)

        except Exception as e:
            self.logger.error(f"Failed to update auth visibility: {e}")

    def _on_browse_key_clicked(self, button) -> None:
        """Handle browse SSH key button click."""
        try:
            file_dialog = Gtk.FileDialog(title=_("Select SSH Key"), modal=True)

            # Set initial folder to ~/.ssh if it exists
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
        """Handle file dialog response."""
        try:
            file = dialog.open_finish(result)
            if file:
                path = file.get_path()
                if path:
                    # Validate selected key
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
        """Handle test SSH connection button click by running it in a background thread."""
        try:
            # 1. Create a temporary session item from the current dialog fields
            test_session = self._create_session_from_fields()
            if not test_session:
                self._show_error_dialog(_("Validation Error"), _("Please fill in all required SSH fields first."))
                return

            # 2. Show a "Testing..." dialog to the user
            self.testing_dialog = Adw.MessageDialog(
                transient_for=self,
                title=_("Testing Connection..."),
                body=_("Attempting to connect to {host}...").format(host=test_session.host)
            )
            spinner = Gtk.Spinner(spinning=True, halign=Gtk.Align.CENTER, margin_top=12)
            self.testing_dialog.set_extra_child(spinner)
            self.testing_dialog.present()

            # 3. Run the actual test in a separate thread
            thread = threading.Thread(target=self._run_test_in_thread, args=(test_session,))
            thread.start()

        except Exception as e:
            self.logger.error(f"Test connection setup failed: {e}")
            if hasattr(self, 'testing_dialog'):
                self.testing_dialog.close()
            self._show_error_dialog(
                _("Test Connection Error"), _("Failed to start connection test: {}").format(e)
            )

    def _create_session_from_fields(self) -> Optional[SessionItem]:
        """Creates a temporary SessionItem from the current dialog fields for testing."""
        if not self.host_entry.get_text().strip() or not self.user_entry.get_text().strip():
            return None

        return SessionItem(
            name="Test Connection",
            session_type="ssh",
            host=self.host_entry.get_text().strip(),
            user=self.user_entry.get_text().strip(),
            port=int(self.port_entry.get_value()),
            auth_type="key" if self.auth_combo.get_selected() == 0 else "password",
            auth_value=self._get_auth_value()
        )

    def _run_test_in_thread(self, test_session: SessionItem):
        """Worker function to be executed in a background thread."""
        spawner = get_spawner()
        success, message = spawner.test_ssh_connection(test_session)
        
        # Schedule the result handling back on the main GTK thread
        GLib.idle_add(self._on_test_finished, success, message)

    def _on_test_finished(self, success: bool, message: str):
        """Callback executed on the main thread after the test completes."""
        # Close the "Testing..." dialog
        if hasattr(self, 'testing_dialog'):
            self.testing_dialog.close()

        if success:
            # Show success message
            result_dialog = Adw.MessageDialog(
                transient_for=self,
                title=_("Connection Successful"),
                body=_("Successfully connected to the SSH server.")
            )
            result_dialog.add_response("ok", _("OK"))
            result_dialog.present()
        else:
            # Show failure message with details
            self._show_error_dialog(
                _("Connection Failed"),
                _("Could not connect to the SSH server."),
                details=message
            )
        return False # Do not repeat idle_add

    def _show_test_connection_dialog(self) -> None:
        """Show SSH connection test dialog."""
        try:
            # Create test session for validation
            test_session = SessionItem(
                name="Test",
                session_type="ssh",
                host=self.host_entry.get_text().strip(),
                user=self.user_entry.get_text().strip(),
                auth_type="key" if self.auth_combo.get_selected() == 0 else "password",
                auth_value=self.key_path_entry.get_text().strip()
                if self.auth_combo.get_selected() == 0
                else self.password_entry.get_text(),
            )

            # Validate session data
            is_valid, errors = validate_session_data(test_session.to_dict())

            if not is_valid:
                error_msg = _("Connection test failed:\n{}").format("\n".join(errors))
                self._show_error_dialog(_("Connection Test Failed"), error_msg)
                return

            # Show placeholder success dialog
            dialog = Adw.MessageDialog(
                transient_for=self,
                title=_("Connection Test"),
                body=_(
                    "SSH connection parameters appear valid.\n\nNote: Actual connection testing will be implemented in a future version."
                ),
            )
            dialog.add_response("ok", _("OK"))
            dialog.present()

        except Exception as e:
            self.logger.error(f"Connection test dialog failed: {e}")
            self._show_error_dialog(_("Test Error"), _("Connection test failed"))

    def _on_cancel_clicked(self, button) -> None:
        """Handle cancel button click with unsaved changes warning."""
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
        """Handle save button click with comprehensive validation."""
        try:
            if not self._validate_and_save():
                return

            # Save to storage
            try:
                save_sessions_and_folders(self.session_store, self.folder_store)

                # Create backup after successful save
                self._create_save_backup()

                # Log session event
                event_type = "created" if self.is_new_item else "modified"
                log_session_event(event_type, self.editing_session.name)

                # Refresh parent if possible
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
        """Validate all input and save changes with comprehensive checks."""
        try:
            self._clear_validation_errors()

            # Basic validation
            if not self._validate_basic_fields():
                return False

            # SSH-specific validation
            if self.type_combo.get_selected() == 1:  # SSH
                if not self._validate_ssh_fields():
                    return False

            # Security validation removed

            # Apply changes to session
            self._apply_changes_to_session()

            # Final validation of complete session
            if not self.editing_session.validate():
                errors = self.editing_session.get_validation_errors()
                self._show_error_dialog(
                    _("Validation Error"),
                    _("Session validation failed:\n{}").format("\n".join(errors)),
                )
                return False

            # Save changes
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
        """Validate basic required fields."""
        valid = True

        # Session name
        if not self._validate_required_field(self.name_entry, _("Session name")):
            valid = False

        return valid

    def _validate_ssh_fields(self) -> bool:
        """Validate SSH-specific fields."""
        valid = True

        # Host validation
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

        # Authentication validation
        if self.auth_combo.get_selected() == 0:  # SSH Key
            key_path = self.key_path_entry.get_text().strip()
            if key_path:
                try:
                    validate_ssh_key_file(key_path)
                    self.key_path_entry.remove_css_class("error")
                except SSHKeyError as e:
                    self.key_path_entry.add_css_class("error")
                    self._validation_errors.append(e.user_message)
                    valid = False

        # Show validation errors
        if not valid and self._validation_errors:
            error_msg = _("SSH configuration errors:\n{}").format(
                "\n".join(self._validation_errors)
            )
            self._show_error_dialog(_("SSH Validation Error"), error_msg)

        return valid

    def _get_auth_value(self) -> str:
        """Get current authentication value."""
        if self.type_combo.get_selected() == 0:  # Local
            return ""
        elif self.auth_combo.get_selected() == 0:  # SSH Key
            return self.key_path_entry.get_text().strip()
        else:  # Password
            return self.password_entry.get_text()

    def _apply_changes_to_session(self) -> None:
        """Apply form values to the editing session."""
        try:
            # Basic properties
            self.editing_session.name = self.name_entry.get_text().strip()
            self.editing_session.session_type = (
                "local" if self.type_combo.get_selected() == 0 else "ssh"
            )

            # Folder path
            if self.folder_combo:
                selected_item = self.folder_combo.get_selected_item()
                if selected_item:
                    display_name = selected_item.get_string()
                    self.editing_session.folder_path = self.folder_paths_map.get(
                        display_name, ""
                    )

            # SSH configuration
            if self.editing_session.is_ssh():
                self.editing_session.host = self.host_entry.get_text().strip()
                self.editing_session.user = self.user_entry.get_text().strip()
                self.editing_session.port = int(self.port_entry.get_value())
                self.editing_session.auth_type = (
                    "key" if self.auth_combo.get_selected() == 0 else "password"
                )
                self.editing_session.auth_value = self._get_auth_value()
            else:
                # Clear SSH fields for local sessions
                self.editing_session.host = ""
                self.editing_session.user = ""
                self.editing_session.auth_type = ""
                self.editing_session.auth_value = ""

        except Exception as e:
            self.logger.error(f"Failed to apply changes to session: {e}")
            raise

    def _update_original_session(self) -> None:
        """Update the original session with changes."""
        try:
            # Copy all properties from editing session to original
            self.original_session.name = self.editing_session.name
            self.original_session.session_type = self.editing_session.session_type
            self.original_session.host = self.editing_session.host
            self.original_session.user = self.editing_session.user
            self.original_session.port = self.editing_session.port
            self.original_session.auth_type = self.editing_session.auth_type
            self.original_session.auth_value = self.editing_session.auth_value
            self.original_session.folder_path = self.editing_session.folder_path

        except Exception as e:
            self.logger.error(f"Failed to update original session: {e}")
            raise

    def _create_save_backup(self) -> None:
        """Create backup after successful save."""
        try:
            backup_manager = get_backup_manager()
            if backup_manager and self.config_paths.SESSIONS_FILE.exists():
                backup_id = backup_manager.create_backup(
                    [self.config_paths.SESSIONS_FILE],
                    BackupType.AUTOMATIC,
                    _("Session {} {}").format(
                        "created" if self.is_new_item else "modified",
                        self.editing_session.name,
                    ),
                )
                if backup_id:
                    self.logger.debug(f"Created session backup: {backup_id}")

        except Exception as e:
            self.logger.warning(f"Failed to create session backup: {e}")


class FolderEditDialog(BaseDialog):
    def __init__(self, parent_window, folder_store, folder_item: Optional[SessionFolder] = None,
                 position: Optional[int] = None, is_new: bool = False): 
        """
        Initialize folder edit dialog.
        
        Args:
            parent_window: Parent window
            folder_store: Store containing folders
            folder_item: SessionFolder to edit (None for new)
            position: Position in store (None for new)
        """
        self.is_new_item = is_new
        title = _("Add Folder") if self.is_new_item else _("Edit Folder")
        
        super().__init__(
            parent_window,
            title,
            default_width=420,
            default_height=380
        )
        
        # Folder management
        self.folder_store = folder_store
        self.original_folder = folder_item if not self.is_new_item else None
        self.editing_folder = SessionFolder.from_dict(folder_item.to_dict()) if not self.is_new_item else folder_item
        self.position = position
        self.old_path = folder_item.path if folder_item else None
        
        # Store original data for change detection
        self._original_data = self.editing_folder.to_dict()
        
        # UI components
        self.name_entry: Optional[Gtk.Entry] = None
        self.parent_combo: Optional[Gtk.DropDown] = None
        
        # Parent folder mapping
        self.parent_paths_map: Dict[str, str] = {}
        
        # Initialize UI
        self._setup_ui()

        # Connect to map signal to set focus after the dialog is shown
        self.connect("map", self._on_map)

        self.logger.info(f"Folder edit dialog opened: {self.editing_folder.name} ({'new' if self.is_new_item else 'edit'})")

    def _on_map(self, widget):
        """Set focus when the dialog is mapped."""
        if self.name_entry:
            self.name_entry.grab_focus()

    def _setup_ui(self) -> None:
        """Set up the dialog user interface."""
        try:
            main_box = Gtk.Box(
                orientation=Gtk.Orientation.VERTICAL,
                spacing=20,
                margin_top=24,
                margin_bottom=24,
                margin_start=24,
                margin_end=24,
            )

            # Folder information group
            folder_group = Adw.PreferencesGroup(title=_("Folder Information"))

            # Folder name
            self._create_name_row(folder_group)

            # Parent folder
            self._create_parent_row(folder_group)

            main_box.append(folder_group)

            # Action bar
            action_bar = self._create_action_bar()

            # Layout
            content_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
            content_box.append(main_box)
            content_box.append(action_bar)

            self.set_content(content_box)

            self.logger.debug("Folder edit dialog UI setup completed")

        except Exception as e:
            self.logger.error(f"Failed to setup UI: {e}")
            self._show_error_dialog(
                _("UI Error"), _("Failed to initialize dialog interface")
            )
            self.close()

    def _create_name_row(self, parent: Adw.PreferencesGroup) -> None:
        """Create folder name input row."""
        try:
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
            # Connects the Enter key to save.
            self.name_entry.connect("activate", self._on_save_clicked)

            name_row.add_suffix(self.name_entry)
            name_row.set_activatable_widget(self.name_entry)
            parent.add(name_row)

        except Exception as e:
            self.logger.error(f"Failed to create name row: {e}")
            raise DialogError("name_row", str(e))

    def _create_parent_row(self, parent: Adw.PreferencesGroup) -> None:
        """Create parent folder selection row."""
        try:
            parent_row = Adw.ComboRow(
                title=_("Parent Folder"),
                subtitle=_("Choose a parent folder for organization"),
            )

            # Build parent folder model
            parent_model = Gtk.StringList()
            parent_model.append(_("Root"))
            self.parent_paths_map = {_("Root"): ""}

            # Add available parent folders (excluding self and descendants)
            available_parents = []
            for i in range(self.folder_store.get_n_items()):
                folder = self.folder_store.get_item(i)
                if isinstance(folder, SessionFolder):
                    # Exclude self and descendants from parent options
                    if not self.is_new_item and (
                        folder.path == self.editing_folder.path
                        or folder.path.startswith(self.editing_folder.path + "/")
                    ):
                        continue
                    available_parents.append(folder)

            sorted_parents = sorted(available_parents, key=lambda f: f.path)
            for folder in sorted_parents:
                # Create indented display name
                depth = folder.path.count("/")
                display_name = f"{'  ' * depth}{folder.name}"
                parent_model.append(display_name)
                self.parent_paths_map[display_name] = folder.path

            parent_row.set_model(parent_model)

            # Set current selection
            selected_index = 0
            if self.editing_folder.parent_path:
                for i, (display, path_val) in enumerate(self.parent_paths_map.items()):
                    if path_val == self.editing_folder.parent_path:
                        selected_index = i
                        break

            parent_row.set_selected(selected_index)
            parent_row.connect("notify::selected", self._on_parent_changed)

            self.parent_combo = parent_row
            parent.add(parent_row)

        except Exception as e:
            self.logger.error(f"Failed to create parent row: {e}")
            raise DialogError("parent_row", str(e))

    def _create_action_bar(self) -> Gtk.ActionBar:
        """Create action bar with buttons."""
        try:
            action_bar = Gtk.ActionBar()

            # Cancel button
            cancel_button = Gtk.Button(label=_("Cancel"))
            cancel_button.connect("clicked", self._on_cancel_clicked)
            action_bar.pack_start(cancel_button)

            # Save button
            save_button = Gtk.Button(label=_("Save"), css_classes=["suggested-action"])
            save_button.connect("clicked", self._on_save_clicked)
            action_bar.pack_end(save_button)

            # Sets the Save button as the default widget for the Enter key
            self.set_default_widget(save_button)

            return action_bar

        except Exception as e:
            self.logger.error(f"Failed to create action bar: {e}")
            raise DialogError("action_bar", str(e))

    # Event handlers
    def _on_name_changed(self, entry: Gtk.Entry) -> None:
        """Handle folder name change."""
        try:
            entry.remove_css_class("error")
            self._mark_changed()
        except Exception as e:
            self.logger.error(f"Name change handling failed: {e}")

    def _on_parent_changed(self, combo_row, param) -> None:
        """Handle parent folder change."""
        try:
            self._mark_changed()
        except Exception as e:
            self.logger.error(f"Parent change handling failed: {e}")

    def _on_cancel_clicked(self, button) -> None:
        """Handle cancel button click."""
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
        """Handle save button click."""
        try:
            # Get SessionOperations instance from the parent window
            operations = self.parent_window.session_tree.operations

            # Create a temporary folder object with the new data
            updated_folder = self._build_updated_folder()
            if not updated_folder:
                return # Validation failed in build method

            result = None
            if self.is_new_item:
                result = operations.add_folder(updated_folder)
            else:
                result = operations.update_folder(self.position, updated_folder)
            
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
        """Validate form and build a new SessionFolder object with the updated data."""
        self._clear_validation_errors()
        if not self._validate_required_field(self.name_entry, _("Folder name")):
            return None
        
        name = self.name_entry.get_text().strip()
        
        selected_item = self.parent_combo.get_selected_item()
        parent_path = ""
        if selected_item:
            display_name = selected_item.get_string()
            parent_path = self.parent_paths_map.get(display_name, "")
            
        new_path = normalize_path(f"{parent_path}/{name}" if parent_path else f"/{name}")
        
        # Create a new object with updated data, preserving metadata
        updated_data = self.editing_folder.to_dict()
        updated_data.update({
            "name": name,
            "parent_path": parent_path,
            "path": str(new_path)
        })
        return SessionFolder.from_dict(updated_data)

    def _create_save_backup(self) -> None:
        """Create backup after successful save."""
        try:
            backup_manager = get_backup_manager()
            if backup_manager and self.config_paths.SESSIONS_FILE.exists():
                backup_id = backup_manager.create_backup(
                    [self.config_paths.SESSIONS_FILE],
                    BackupType.AUTOMATIC,
                    _("Folder {} {}").format(
                        "created" if self.is_new_item else "modified",
                        self.editing_folder.name,
                    ),
                )
                if backup_id:
                    self.logger.debug(f"Created folder backup: {backup_id}")

        except Exception as e:
            self.logger.warning(f"Failed to create folder backup: {e}")


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
        """
        Initialize preferences dialog.

        Args:
            parent_window: Parent window
            settings_manager: SettingsManager instance
        """
        super().__init__(
            title=_("Preferences"),
            transient_for=parent_window,
            modal=True,
            hide_on_close=True,
            default_width=600,
            default_height=700,
        )

        # Initialize components
        self.logger = get_logger("ashyterm.ui.dialogs.preferences")
        self.settings_manager = settings_manager
        self.platform_info = get_platform_info()

        # UI tracking
        self.shortcut_rows: Dict[str, Adw.ActionRow] = {}
        self._change_listeners: List[Callable] = []

        # Setup all preference pages
        self._setup_appearance_page()
        self._setup_behavior_page()
        self._setup_shortcuts_page()
        self._setup_backup_page()
        self._setup_advanced_page()

        self.logger.info("Preferences dialog initialized")

    def _setup_appearance_page(self) -> None:
        """Set up appearance preferences page."""
        try:
            appearance_page = Adw.PreferencesPage(
                title=_("Appearance"), icon_name="preferences-color-symbolic"
            )
            self.add(appearance_page)

            # Colors & Font group
            colors_group = Adw.PreferencesGroup(
                title=_("Colors & Font"),
                description=_("Customize terminal appearance and typography"),
            )
            appearance_page.add(colors_group)

            # Color scheme
            color_scheme_row = Adw.ComboRow(
                title=_("Color Scheme"), subtitle=_("Select terminal color scheme")
            )

            schemes = ColorSchemes.get_schemes()
            scheme_names = [
                schemes[name]["name"] for name in ColorSchemeMap.get_schemes_list()
            ]
            color_scheme_row.set_model(Gtk.StringList.new(scheme_names))
            color_scheme_row.set_selected(self.settings_manager.get("color_scheme", 0))
            color_scheme_row.connect("notify::selected", self._on_color_scheme_changed)
            colors_group.add(color_scheme_row)

            # Transparency
            transparency_row = Adw.ActionRow(
                title=_("Background Transparency"),
                subtitle=_("Adjust terminal background transparency"),
            )

            self.transparency_scale = Gtk.Scale.new_with_range(
                Gtk.Orientation.HORIZONTAL, 0, 100, 1
            )
            self.transparency_scale.set_value(
                self.settings_manager.get("transparency", 0)
            )
            self.transparency_scale.set_draw_value(True)
            self.transparency_scale.set_value_pos(Gtk.PositionType.RIGHT)
            self.transparency_scale.set_hexpand(True)
            self.transparency_scale.connect(
                "value-changed", self._on_transparency_changed
            )

            transparency_row.add_suffix(self.transparency_scale)
            transparency_row.set_activatable_widget(self.transparency_scale)
            colors_group.add(transparency_row)

            font_row = Adw.ActionRow(
                title=_("Terminal Font"),
                subtitle=_("Select font family and size for terminal text"),
            )
            
            font_button = Gtk.FontButton()
            font_button.set_font(self.settings_manager.get("font", "Monospace 10"))
            font_button.connect("font-set", self._on_font_changed)
            
            font_row.add_suffix(font_button)
            font_row.set_activatable_widget(font_button)
            colors_group.add(font_row)

        except Exception as e:
            self.logger.error(f"Failed to setup appearance page: {e}")
    def _setup_behavior_page(self) -> None:
        """Set up behavior preferences page."""
        try:
            behavior_page = Adw.PreferencesPage(
                title=_("Behavior"), icon_name="preferences-system-symbolic"
            )
            self.add(behavior_page)

            # Terminal behavior group
            terminal_group = Adw.PreferencesGroup(
                title=_("Terminal Behavior"),
                description=_("Configure how terminals behave"),
            )
            behavior_page.add(terminal_group)

            # Auto close tabs
            auto_close_row = Adw.SwitchRow(
                title=_("Auto Close Tabs"),
                subtitle=_("Automatically close tabs when process exits normally"),
            )
            auto_close_row.set_active(self.settings_manager.get("auto_close_tab", True))
            auto_close_row.connect(
                "notify::active",
                lambda r, p: self._on_setting_changed("auto_close_tab", r.get_active()),
            )
            terminal_group.add(auto_close_row)

            # Scroll on output
            scroll_output_row = Adw.SwitchRow(
                title=_("Scroll on Output"),
                subtitle=_("Automatically scroll when new output appears"),
            )
            scroll_output_row.set_active(
                self.settings_manager.get("scroll_on_output", True)
            )
            scroll_output_row.connect(
                "notify::active",
                lambda r, p: self._on_setting_changed(
                    "scroll_on_output", r.get_active()
                ),
            )
            terminal_group.add(scroll_output_row)

            # Scroll on keystroke
            scroll_keystroke_row = Adw.SwitchRow(
                title=_("Scroll on Keystroke"),
                subtitle=_("Automatically scroll when typing"),
            )
            scroll_keystroke_row.set_active(
                self.settings_manager.get("scroll_on_keystroke", True)
            )
            scroll_keystroke_row.connect(
                "notify::active",
                lambda r, p: self._on_setting_changed(
                    "scroll_on_keystroke", r.get_active()
                ),
            )
            terminal_group.add(scroll_keystroke_row)

            # Mouse autohide
            mouse_hide_row = Adw.SwitchRow(
                title=_("Hide Mouse Cursor"),
                subtitle=_("Hide mouse cursor when typing"),
            )
            mouse_hide_row.set_active(self.settings_manager.get("mouse_autohide", True))
            mouse_hide_row.connect(
                "notify::active",
                lambda r, p: self._on_setting_changed("mouse_autohide", r.get_active()),
            )
            terminal_group.add(mouse_hide_row)

            # Cursor blink
            cursor_blink_row = Adw.SwitchRow(
                title=_("Cursor Blinking"),
                subtitle=_("Enable cursor blinking animation"),
            )
            cursor_blink_row.set_active(self.settings_manager.get("cursor_blink", True))
            cursor_blink_row.connect(
                "notify::active",
                lambda r, p: self._on_setting_changed("cursor_blink", r.get_active()),
            )
            terminal_group.add(cursor_blink_row)

            # OSC7 Directory Tracking group
            osc7_group = Adw.PreferencesGroup(
                title=_("Directory Tracking"),
                description=_("Track current directory and update tab titles"),
            )
            behavior_page.add(osc7_group)

            # OSC7 enabled
            osc7_enabled_row = Adw.SwitchRow(
                title=_("Enable Directory Tracking"),
                subtitle=_("Monitor current directory and show it in tab titles"),
            )
            osc7_enabled_row.set_active(self.settings_manager.get("osc7_enabled", True))
            osc7_enabled_row.connect(
                "notify::active",
                lambda r, p: self._on_setting_changed("osc7_enabled", r.get_active()),
            )
            osc7_group.add(osc7_enabled_row)

            # OSC7 show hostname
            osc7_hostname_row = Adw.SwitchRow(
                title=_("Show Hostname in Titles"),
                subtitle=_("Include hostname in tab titles when available"),
            )
            osc7_hostname_row.set_active(
                self.settings_manager.get("osc7_show_hostname", False)
            )
            osc7_hostname_row.connect(
                "notify::active",
                lambda r, p: self._on_setting_changed(
                    "osc7_show_hostname", r.get_active()
                ),
            )
            osc7_group.add(osc7_hostname_row)

            # UI behavior group
            ui_group = Adw.PreferencesGroup(
                title=_("Interface Behavior"),
                description=_("Configure application interface behavior")
            )
            behavior_page.add(ui_group)
            
            # Confirm close
            confirm_close_row = Adw.SwitchRow(
                title=_("Confirm Application Exit"),
                subtitle=_("Show confirmation dialog when closing the application")
            )
            confirm_close_row.set_active(self.settings_manager.get("confirm_close", True))
            confirm_close_row.connect("notify::active", lambda r, p: self._on_setting_changed("confirm_close", r.get_active()))
            ui_group.add(confirm_close_row)
            
            # Confirm delete
            confirm_delete_row = Adw.SwitchRow(
                title=_("Confirm Deletion"),
                subtitle=_("Show confirmation dialog when deleting sessions or folders")
            )
            confirm_delete_row.set_active(self.settings_manager.get("confirm_delete", True))
            confirm_delete_row.connect("notify::active", lambda r, p: self._on_setting_changed("confirm_delete", r.get_active()))
            ui_group.add(confirm_delete_row)
            
        except Exception as e:
            self.logger.error(f"Failed to setup behavior page: {e}")
    
    def _setup_shortcuts_page(self) -> None:
        """Set up keyboard shortcuts page."""
        try:
            shortcuts_page = Adw.PreferencesPage(
                title=_("Shortcuts"),
                icon_name="preferences-desktop-keyboard-shortcuts-symbolic"
            )
            self.add(shortcuts_page)
            
            # Terminal shortcuts group
            terminal_group = Adw.PreferencesGroup(
                title=_("Terminal Actions"),
                description=_("Keyboard shortcuts for terminal operations")
            )
            shortcuts_page.add(terminal_group)
            
            # Application shortcuts group
            app_group = Adw.PreferencesGroup(
                title=_("Application Actions"),
                description=_("Keyboard shortcuts for application operations")
            )
            shortcuts_page.add(app_group)
            
            # Define shortcut categories
            terminal_shortcuts = {
                "new-local-tab": _("New Tab"),
                "close-tab": _("Close Tab"), 
                "copy": _("Copy"),
                "paste": _("Paste"),
                "select-all": _("Select All")
            }
            
            app_shortcuts = {
                "preferences": _("Preferences"), 
                "quit": _("Quit Application"),
                "toggle-sidebar": _("Toggle Sidebar"),
                "new-window": _("New Window")
            }
            
            # Create shortcut rows
            self._create_shortcut_rows(terminal_group, terminal_shortcuts)
            self._create_shortcut_rows(app_group, app_shortcuts)
            
        except Exception as e:
            self.logger.error(f"Failed to setup shortcuts page: {e}")
    
    def _create_shortcut_rows(self, group: Adw.PreferencesGroup, shortcuts: Dict[str, str]) -> None:
        """Create shortcut rows for a group."""
        try:
            for key, title in shortcuts.items():
                current_shortcut = self.settings_manager.get_shortcut(key)
                
                # Get display label
                subtitle = current_shortcut if current_shortcut else _("None")
                try:
                    if current_shortcut:
                        subtitle = accelerator_to_label(current_shortcut)
                except Exception as e:
                    self.logger.debug(f"Error getting accelerator label for {key}: {e}")
                
                row = Adw.ActionRow(
                    title=title,
                    subtitle=subtitle
                )
                
                button = Gtk.Button(
                    label=_("Edit"),
                    css_classes=["flat"]
                )
                button.connect("clicked", self._on_shortcut_edit_clicked, key, row)
                
                row.add_suffix(button)
                row.set_activatable_widget(button)
                group.add(row)
                
                self.shortcut_rows[key] = row
                
        except Exception as e:
            self.logger.error(f"Failed to create shortcut rows: {e}")
    
    def _setup_backup_page(self) -> None:
        """Set up backup preferences page."""
        try:
            backup_page = Adw.PreferencesPage(
                title=_("Backup"),
                icon_name="folder-download-symbolic"
            )
            self.add(backup_page)
            
            # Backup settings group
            backup_group = Adw.PreferencesGroup(
                title=_("Backup Settings"),
                description=_("Configure automatic backup and recovery options")
            )
            backup_page.add(backup_group)
            
            # Auto backup enabled
            auto_backup_row = Adw.SwitchRow(
                title=_("Automatic Backups"),
                subtitle=_("Automatically create backups of sessions and settings")
            )
            auto_backup_row.set_active(self.settings_manager.get("auto_backup_enabled", True))
            auto_backup_row.connect("notify::active", lambda r, p: self._on_setting_changed("auto_backup_enabled", r.get_active()))
            backup_group.add(auto_backup_row)

            # Backup on change
            change_backup_row = Adw.SwitchRow(
                title=_("Backup on Change"),
                subtitle=_("Create a backup every time sessions or folders are modified")
            )
            change_backup_row.set_active(self.settings_manager.get("backup_on_change", True))
            change_backup_row.connect("notify::active", lambda r, p: self._on_setting_changed("backup_on_change", r.get_active()))
            backup_group.add(change_backup_row)
            
            # Backup on exit
            exit_backup_row = Adw.SwitchRow(
                title=_("Backup on Exit"),
                subtitle=_("Create backup when application exits")
            )
            exit_backup_row.set_active(self.settings_manager.get("backup_on_exit", False))
            exit_backup_row.connect("notify::active", lambda r, p: self._on_setting_changed("backup_on_exit", r.get_active()))
            backup_group.add(exit_backup_row)
            
            # Backup interval
            interval_row = Adw.SpinRow(
                title=_("Backup Interval"),
                subtitle=_("Hours between automatic backups"),
                adjustment=Gtk.Adjustment(value=self.settings_manager.get("backup_interval_hours", 24), 
                                          lower=1, upper=168, step_increment=1)
            )
            interval_row.connect("notify::value", lambda s, p: self._on_setting_changed("backup_interval_hours", int(s.get_value())))
            backup_group.add(interval_row)
            
            # Retention days
            retention_row = Adw.SpinRow(
                title=_("Retention Period"),
                subtitle=_("Days to keep old backups"),
                adjustment=Gtk.Adjustment(value=self.settings_manager.get("backup_retention_days", 30),
                                          lower=1, upper=365, step_increment=1)
            )
            retention_row.connect("notify::value", lambda s, p: self._on_setting_changed("backup_retention_days", int(s.get_value())))
            backup_group.add(retention_row)
            
        except Exception as e:
            self.logger.error(f"Failed to setup backup page: {e}")
    
    def _setup_advanced_page(self) -> None:
        """Set up advanced preferences page."""
        try:
            advanced_page = Adw.PreferencesPage(
                title=_("Advanced"),
                icon_name="preferences-other-symbolic"
            )
            self.add(advanced_page)
            
            # Development group
            dev_group = Adw.PreferencesGroup(
                title=_("Development & Debugging"),
                description=_("Advanced options for development and troubleshooting")
            )
            advanced_page.add(dev_group)
            
            # Debug mode
            debug_row = Adw.SwitchRow(
                title=_("Debug Mode"),
                subtitle=_("Enable verbose logging and debug features")
            )
            debug_row.set_active(self.settings_manager.get("debug_mode", False))
            debug_row.connect("notify::active", lambda r, p: self._on_setting_changed("debug_mode", r.get_active()))
            dev_group.add(debug_row)
            
            # Performance mode
            performance_row = Adw.SwitchRow(
                title=_("Performance Mode"),
                subtitle=_("Optimize for performance over features")
            )
            performance_row.set_active(self.settings_manager.get("performance_mode", False))
            performance_row.connect("notify::active", lambda r, p: self._on_setting_changed("performance_mode", r.get_active()))
            dev_group.add(performance_row)
            
            # Experimental features
            experimental_row = Adw.SwitchRow(
                title=_("Experimental Features"),
                subtitle=_("Enable experimental and unstable features")
            )
            experimental_row.set_active(self.settings_manager.get("experimental_features", False))
            experimental_row.connect("notify::active", lambda r, p: self._on_setting_changed("experimental_features", r.get_active()))
            dev_group.add(experimental_row)
            
            # Reset group
            reset_group = Adw.PreferencesGroup(
                title=_("Reset"),
                description=_("Reset application settings to defaults")
            )
            advanced_page.add(reset_group)
            
            # Reset button
            reset_row = Adw.ActionRow(
                title=_("Reset All Settings"),
                subtitle=_("Restore all settings to their default values")
            )
            
            reset_button = Gtk.Button(
                label=_("Reset"),
                css_classes=["destructive-action"]
            )
            reset_button.connect("clicked", self._on_reset_settings_clicked)
            
            reset_row.add_suffix(reset_button)
            reset_row.set_activatable_widget(reset_button)
            reset_group.add(reset_row)
            
        except Exception as e:
            self.logger.error(f"Failed to setup advanced page: {e}")
    
    # Event handlers
    def _on_color_scheme_changed(self, combo_row, param) -> None:
        """Handle color scheme change."""
        try:
            index = combo_row.get_selected()
            self.settings_manager.set("color_scheme", index)
            self.emit("color-scheme-changed", index)
            self.logger.debug(f"Color scheme changed to index {index}")
        except Exception as e:
            self.logger.error(f"Color scheme change failed: {e}")
    
    def _on_transparency_changed(self, scale) -> None:
        """Handle transparency change."""
        try:
            value = scale.get_value()
            self.settings_manager.set("transparency", value)
            self.emit("transparency-changed", value)
            self.logger.debug(f"Transparency changed to {value}")
        except Exception as e:
            self.logger.error(f"Transparency change failed: {e}")
            
    def _on_blur_changed(self, scale) -> None:
        """Handle blur intensity change."""
        try:
            value = scale.get_value()
            self.settings_manager.set("terminal_blur", value)
            self.emit("blur-changed", value)
            self.logger.debug(f"Terminal blur changed to {value}px")
        except Exception as e:
            self.logger.error(f"Blur change failed: {e}")
    
    def _on_font_changed(self, font_button) -> None:
        """Handle font change when user confirms selection in the font dialog."""
        try:
            font = font_button.get_font()
            self.settings_manager.set("font", font)
            self.emit("font-changed", font)
            self.logger.debug(f"Font changed to {font}")
        except Exception as e:
            self.logger.error(f"Font change failed: {e}")
             
    def _on_setting_changed(self, key: str, value: Any) -> None:
        """Handle generic setting change."""
        try:
            self.settings_manager.set(key, value)
            self.emit("setting-changed", key, value)
            self.logger.debug(f"Setting changed: {key} = {value}")
        except Exception as e:
            self.logger.error(f"Setting change failed for {key}: {e}")
    
    def _on_shortcut_edit_clicked(self, button, shortcut_key: str, row: Adw.ActionRow) -> None:
        """Handle shortcut edit button click."""
        try:
            dialog = Adw.MessageDialog(
                transient_for=self,
                title=_("Edit Shortcut"),
                body=_("Press the new key combination for '{}' or Esc to cancel.").format(row.get_title())
            )
            
            current_shortcut = self.settings_manager.get_shortcut(shortcut_key)
            
            # Display current shortcut
            current_label = current_shortcut if current_shortcut else _("None")
            try:
                if current_shortcut:
                    current_label = accelerator_to_label(current_shortcut)
            except Exception as e:
                self.logger.debug(f"Error getting current accelerator label: {e}")
            
            feedback_label = Gtk.Label(label=_("Current: {}\nNew: (press keys)").format(current_label))
            dialog.set_extra_child(feedback_label)
            
            # Set up key capture
            key_controller = Gtk.EventControllerKey()
            new_shortcut = [None]
            
            def on_key_pressed(controller, keyval, keycode, state):
                # Ignore modifier-only keys
                if keyval in (Gdk.KEY_Control_L, Gdk.KEY_Control_R, Gdk.KEY_Shift_L, 
                             Gdk.KEY_Shift_R, Gdk.KEY_Alt_L, Gdk.KEY_Alt_R,
                             Gdk.KEY_Super_L, Gdk.KEY_Super_R):
                    return Gdk.EVENT_PROPAGATE
                
                if keyval == Gdk.KEY_Escape:
                    new_shortcut[0] = "cancel"
                    dialog.response("cancel")
                    return Gdk.EVENT_STOP
                
                # Build accelerator string
                try:
                    shortcut_string = Gtk.accelerator_name(keyval, state & Gtk.accelerator_get_default_mod_mask())
                    new_shortcut[0] = shortcut_string
                    
                    # Update feedback
                    label_text = shortcut_string
                    try:
                        label_text = accelerator_to_label(shortcut_string)
                    except Exception as e:
                        self.logger.debug(f"Error getting new accelerator label: {e}")
                    
                    feedback_label.set_label(_("Current: {}\nNew: {}").format(current_label, label_text))
                    
                except Exception as e:
                    self.logger.error(f"Error building accelerator name: {e}")
                    new_shortcut[0] = "cancel"
                
                return Gdk.EVENT_STOP
            
            key_controller.connect("key-pressed", on_key_pressed)
            dialog.add_controller(key_controller)
            
            # Add responses
            dialog.add_response("cancel", _("Cancel"))
            dialog.add_response("clear", _("Clear"))
            dialog.add_response("save", _("Set Shortcut"))
            dialog.set_default_response("save")
            dialog.set_response_appearance("save", Adw.ResponseAppearance.SUGGESTED)
            dialog.set_response_appearance("clear", Adw.ResponseAppearance.DESTRUCTIVE)
            
            def on_response(dlg, response_id):
                try:
                    if response_id == "save" and new_shortcut[0] and new_shortcut[0] != "cancel":
                        # Save new shortcut
                        self.settings_manager.set_shortcut(shortcut_key, new_shortcut[0])
                        
                        # Update row subtitle
                        label = new_shortcut[0]
                        try:
                            label = accelerator_to_label(new_shortcut[0])
                        except Exception as e:
                            self.logger.debug(f"Error getting final accelerator label: {e}")
                        
                        row.set_subtitle(label)
                        self.emit("shortcut-changed")
                        
                    elif response_id == "clear":
                        # Clear shortcut
                        self.settings_manager.set_shortcut(shortcut_key, "")
                        row.set_subtitle(_("None"))
                        self.emit("shortcut-changed")
                        
                except Exception as e:
                    self.logger.error(f"Error saving shortcut: {e}")
                
                dlg.close()
            
            dialog.connect("response", on_response)
            dialog.present()
            
        except Exception as e:
            self.logger.error(f"Shortcut edit dialog failed: {e}")
    
    def _on_reset_settings_clicked(self, button) -> None:
        """Handle reset settings button click."""
        try:
            dialog = Adw.MessageDialog(
                transient_for=self,
                title=_("Reset All Settings"),
                body=_("Are you sure you want to reset all settings to their default values? This action cannot be undone.")
            )
            
            dialog.add_response("cancel", _("Cancel"))
            dialog.add_response("reset", _("Reset All Settings"))
            dialog.set_response_appearance("reset", Adw.ResponseAppearance.DESTRUCTIVE)
            
            def on_response(dlg, response_id):
                if response_id == "reset":
                    try:
                        self.settings_manager.reset_to_defaults()
                        
                        # Show success message
                        success_dialog = Adw.MessageDialog(
                            transient_for=self,
                            title=_("Settings Reset"),
                            body=_("All settings have been reset to their default values. Please restart the application for all changes to take effect.")
                        )
                        success_dialog.add_response("ok", _("OK"))
                        success_dialog.present()
                        
                        self.logger.info("All settings reset to defaults")
                        
                    except Exception as e:
                        self.logger.error(f"Failed to reset settings: {e}")
                        error_dialog = Adw.MessageDialog(
                            transient_for=self,
                            title=_("Reset Failed"),
                            body=_("Failed to reset settings: {}").format(e)
                        )
                        error_dialog.add_response("ok", _("OK"))
                        error_dialog.present()
                
                dlg.close()
            
            dialog.connect("response", on_response)
            dialog.present()
            
        except Exception as e:
            self.logger.error(f"Reset settings dialog failed: {e}")

class MoveSessionDialog(BaseDialog):
    """A dialog to move a session to a different folder."""

    def __init__(self, parent_window, session_to_move: SessionItem, 
                 folder_store: Gio.ListStore, operations: SessionOperations):
        """
        Initialize the move session dialog.
        
        Args:
            parent_window: The parent CommTerminalWindow.
            session_to_move: The SessionItem instance to be moved.
            folder_store: The Gio.ListStore containing all SessionFolder objects.
            operations: The SessionOperations instance to perform the move.
        """
        title = _("Move Session")
        super().__init__(parent_window, title, default_width=400, default_height=250)

        self.session_to_move = session_to_move
        self.folder_store = folder_store
        self.operations = operations
        
        self.folder_paths_map: Dict[str, str] = {}
        self.folder_combo: Optional[Adw.ComboRow] = None

        self._setup_ui()
        self.logger.info(f"Move session dialog opened for '{session_to_move.name}'")

    def _setup_ui(self):
        """Set up the dialog's user interface."""
        main_box = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=16, margin_top=24, margin_bottom=24, margin_start=24, margin_end=24
        )

        # Main content group
        group = Adw.PreferencesGroup(
            title=_("Select Destination"),
            description=_("Choose the folder to move the session '{name}' to.").format(name=self.session_to_move.name)
        )
        main_box.append(group)

        # Folder selection row
        folder_row = Adw.ComboRow(
            title=_("Destination Folder"),
            subtitle=_("Select a folder or 'Root' for the top level")
        )
        self.folder_combo = folder_row
        group.add(folder_row)

        # Populate the folder list
        self._populate_folder_combo()

        # Action bar
        action_bar = Gtk.ActionBar()
        cancel_button = Gtk.Button(label=_("Cancel"))
        cancel_button.connect("clicked", self._on_cancel_clicked)
        action_bar.pack_start(cancel_button)

        move_button = Gtk.Button(label=_("Move"), css_classes=["suggested-action"])
        move_button.connect("clicked", self._on_move_clicked)
        action_bar.pack_end(move_button)
        self.set_default_widget(move_button)

        # Final layout
        content_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        content_box.append(main_box)
        content_box.append(action_bar)
        self.set_content(content_box)

    def _populate_folder_combo(self):
        """Fill the folder combo box with available folders."""
        folder_model = Gtk.StringList()
        folder_model.append(_("Root"))
        self.folder_paths_map = {_("Root"): ""}

        # Sort folders by path for a hierarchical view
        folders = sorted([self.folder_store.get_item(i) for i in range(self.folder_store.get_n_items())], key=lambda f: f.path)
        
        selected_index = 0
        for folder in folders:
            depth = folder.path.count("/")
            display_name = f"{'  ' * depth}{folder.name}"
            folder_model.append(display_name)
            self.folder_paths_map[display_name] = folder.path
            
            # Find the index of the current folder to pre-select it
            if folder.path == self.session_to_move.folder_path:
                selected_index = folder_model.get_n_items() - 1
        
        self.folder_combo.set_model(folder_model)
        self.folder_combo.set_selected(selected_index)

    def _on_move_clicked(self, button):
        """Handle the move button click."""
        selected_item = self.folder_combo.get_selected_item()
        if not selected_item:
            return

        display_name = selected_item.get_string()
        target_folder_path = self.folder_paths_map.get(display_name, "")

        if target_folder_path == self.session_to_move.folder_path:
            self.logger.debug("Target folder is the same as the source. Closing dialog.")
            self.close()
            return

        result = self.operations.move_session_to_folder(self.session_to_move, target_folder_path)

        if result.success:
            if result.warnings:
                # Show a toast for non-critical warnings (like rename)
                if hasattr(self.parent_window, 'get_toast_overlay') and (overlay := self.parent_window.get_toast_overlay()):
                    overlay.add_toast(Adw.Toast(title=result.warnings[0]))
            
            self.logger.info(f"Session '{self.session_to_move.name}' moved to '{target_folder_path}'")
            self.parent_window.refresh_tree()
            self.close()
        else:
            self._show_error_dialog(_("Move Failed"), result.message)
