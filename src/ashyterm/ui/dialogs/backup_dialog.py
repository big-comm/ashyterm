# ashyterm/ui/dialogs/backup_dialog.py

import threading
from datetime import datetime
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gio, GLib, Gtk

from ...settings.config import LAYOUT_DIR, SESSIONS_FILE, SETTINGS_FILE
from ...utils.translation_utils import _


class BackupRestoreHandler:
    """Handles the UI and flow for application data backup and restore."""

    def __init__(self, application: Adw.Application):
        self.app = application
        self.logger = application.logger if hasattr(application, "logger") else None

    def start_backup_flow(self, parent_window: Gtk.Window):
        """Starts the manual backup creation flow."""
        file_dialog = Gtk.FileDialog(title=_("Save Backup As..."), modal=True)
        timestamp = datetime.now().strftime("%Y-%m-%d")
        file_dialog.set_initial_name(f"ashyterm-backup-{timestamp}.7z")
        file_dialog.save(parent_window, None, self._on_backup_file_selected)

    def _on_backup_file_selected(self, dialog, result):
        """Callback after user selects a location to save the backup."""
        try:
            gio_file = dialog.save_finish(result)
            if gio_file:
                self._prompt_for_backup_password(gio_file.get_path())
        except GLib.Error as e:
            if not e.matches(Gio.io_error_quark(), Gio.IOErrorEnum.CANCELLED):
                self._show_error_dialog(_("Backup Error"), e.message)

    def _prompt_for_backup_password(self, target_path: str):
        """Shows a dialog to get and confirm a password for the backup."""
        active_window = self.app.get_active_window()
        dialog = Adw.MessageDialog(
            transient_for=active_window,
            heading=_("Set Backup Password"),
            body=_("Please enter a password to encrypt the backup file."),
            close_response="cancel",
        )
        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        pass_entry = Gtk.PasswordEntry(
            placeholder_text=_("Password"), show_peek_icon=True
        )
        confirm_entry = Gtk.PasswordEntry(
            placeholder_text=_("Confirm Password"), show_peek_icon=True
        )
        content.append(pass_entry)
        content.append(confirm_entry)
        dialog.set_extra_child(content)
        dialog.add_response("cancel", _("Cancel"))
        dialog.add_response("backup", _("Create Backup"))
        dialog.set_default_response("backup")
        dialog.set_response_appearance("backup", Adw.ResponseAppearance.SUGGESTED)

        def on_response(d, response_id):
            if response_id == "backup":
                pwd1 = pass_entry.get_text()
                pwd2 = confirm_entry.get_text()
                if not pwd1:
                    self._show_error_dialog(
                        _("Password Error"), _("Password cannot be empty."), parent=d
                    )
                    return
                if pwd1 != pwd2:
                    self._show_error_dialog(
                        _("Password Error"), _("Passwords do not match."), parent=d
                    )
                    return

                d.close()
                self._execute_backup(target_path, pwd1)
            else:
                d.close()

        dialog.connect("response", on_response)
        dialog.present()

    def _execute_backup(self, target_path: str, password: str):
        """Executes the backup process in a separate thread."""
        active_window = self.app.get_active_window()
        if not active_window:
            return

        toast = Adw.Toast(title=_("Creating backup..."), timeout=0)
        active_window.toast_overlay.add_toast(toast)

        def backup_thread():
            try:
                source_files = [
                    Path(SESSIONS_FILE),
                    Path(SETTINGS_FILE),
                ]
                layouts_dir = Path(LAYOUT_DIR)
                self.app.backup_manager.create_encrypted_backup(
                    target_path,
                    password,
                    active_window.session_store,
                    source_files,
                    layouts_dir,
                )
                GLib.idle_add(
                    self._show_info_dialog,
                    _("Backup Complete"),
                    _("Backup saved successfully to:\n{}").format(target_path),
                )
            except Exception as e:
                if self.logger:
                    self.logger.error(f"Manual backup failed: {e}")
                GLib.idle_add(
                    self._show_error_dialog,
                    _("Backup Failed"),
                    _("Could not create backup: {}").format(e),
                )
            finally:
                GLib.idle_add(toast.dismiss)

        threading.Thread(target=backup_thread, daemon=True).start()

    def start_restore_flow(self, parent_window: Gtk.Window):
        """Starts the restore backup flow."""
        dialog = Adw.MessageDialog(
            transient_for=parent_window,
            heading=_("Restore from Backup?"),
            body=_(
                "Restoring from a backup will overwrite all your current sessions, settings, and layouts. This action cannot be undone.\n\n<b>The application will need to be restarted after restoring.</b>"
            ),
            body_use_markup=True,
            close_response="cancel",
        )
        dialog.add_response("cancel", _("Cancel"))
        dialog.add_response("restore", _("Choose File and Restore"))
        dialog.set_response_appearance("restore", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.connect("response", self._on_restore_confirmation)
        dialog.present()

    def _on_restore_confirmation(self, dialog, response_id):
        dialog.close()
        if response_id == "restore":
            file_dialog = Gtk.FileDialog(title=_("Select Backup File"), modal=True)
            file_filter = Gtk.FileFilter()
            file_filter.add_pattern("*.7z")
            file_filter.set_name(_("Backup Files"))
            filters = Gio.ListStore.new(Gtk.FileFilter)
            filters.append(file_filter)
            file_dialog.set_filters(filters)
            file_dialog.open(
                self.app.get_active_window(), None, self._on_restore_file_selected
            )

    def _on_restore_file_selected(self, dialog, result):
        """Callback after user selects a backup file to restore."""
        try:
            gio_file = dialog.open_finish(result)
            if gio_file:
                self._prompt_for_restore_password(gio_file.get_path())
        except GLib.Error as e:
            if not e.matches(Gio.io_error_quark(), Gio.IOErrorEnum.CANCELLED):
                self._show_error_dialog(_("Restore Error"), e.message)

    def _prompt_for_restore_password(self, source_path: str):
        """Shows a dialog to get the password for the backup file."""
        dialog = Adw.MessageDialog(
            transient_for=self.app.get_active_window(),
            heading=_("Enter Backup Password"),
            body=_("Please enter the password for the selected backup file."),
            close_response="cancel",
        )
        pass_entry = Gtk.PasswordEntry(
            placeholder_text=_("Password"), show_peek_icon=True
        )
        dialog.set_extra_child(pass_entry)
        dialog.add_response("cancel", _("Cancel"))
        dialog.add_response("restore", _("Restore"))
        dialog.set_default_response("restore")
        dialog.set_response_appearance("restore", Adw.ResponseAppearance.SUGGESTED)

        def on_response(d, response_id):
            if response_id == "restore":
                password = pass_entry.get_text()
                if password:
                    self._execute_restore(source_path, password)
            d.close()

        dialog.connect("response", on_response)
        dialog.present()

    def _execute_restore(self, source_path: str, password: str):
        """Executes the restore process in a separate thread."""
        active_window = self.app.get_active_window()
        toast = Adw.Toast(title=_("Restoring from backup..."), timeout=0)
        active_window.toast_overlay.add_toast(toast)

        def restore_thread():
            try:
                self.app.backup_manager.restore_from_encrypted_backup(
                    source_path, password, self.app.platform_info.config_dir
                )
                GLib.idle_add(self._show_restore_success_dialog)
            except Exception as e:
                if self.logger:
                    self.logger.error(f"Restore failed: {e}")
                GLib.idle_add(
                    self._show_error_dialog,
                    _("Restore Failed"),
                    _("Could not restore from backup: {}").format(e),
                )
            finally:
                GLib.idle_add(toast.dismiss)

        threading.Thread(target=restore_thread, daemon=True).start()

    def _show_restore_success_dialog(self):
        dialog = Adw.MessageDialog(
            transient_for=self.app.get_active_window(),
            heading=_("Restore Complete"),
            body=_(
                "Data has been restored successfully. Please restart Ashy Terminal for the changes to take effect."
            ),
            close_response="ok",
        )
        dialog.add_response("ok", _("OK"))
        dialog.present()
        return False

    def _show_error_dialog(self, title: str, message: str, parent=None) -> None:
        """Show error dialog to user."""
        if parent is None:
            parent = self.app.get_active_window()
        dialog = Adw.MessageDialog(transient_for=parent, title=title, body=message)
        dialog.add_response("ok", _("OK"))
        dialog.present()

    def _show_info_dialog(self, title: str, message: str) -> None:
        """Show info dialog to user."""
        parent = self.app.get_active_window()
        dialog = Adw.MessageDialog(transient_for=parent, title=title, body=message)
        dialog.add_response("ok", _("OK"))
        dialog.present()
