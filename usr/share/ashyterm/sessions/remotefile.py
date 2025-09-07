# ashyterm/filemanager/remotefile.py

import shutil
import tempfile
import threading
from functools import partial
from pathlib import Path
from typing import TYPE_CHECKING, Optional

from gi.repository import Adw, Gio, Gtk

from ..utils.logger import get_logger
from ..utils.translation_utils import _

if TYPE_CHECKING:
    from .manager import FileManager


class RemoteEditManager:
    """Manages the lifecycle of editing remote files locally."""

    def __init__(self, manager: "FileManager"):
        self.logger = get_logger("ashyterm.filemanager.remotefile")
        self.manager = manager
        self.parent_window = manager.parent_window
        self.operations = manager.operations
        self.transfer_manager = manager.transfer_manager

        self.remote_edit_dir = self.manager.config_dir / "remote_edit_tmp"
        self.remote_edit_dir.mkdir(exist_ok=True)

        self.file_monitors = {}
        self.edited_file_metadata = {}

    def edit_remote_file(self, file_item, app_info: Optional[Gio.AppInfo] = None):
        """Downloads a remote file to a temporary location for editing."""
        remote_path = f"{self.manager.current_path.rstrip('/')}/{file_item.name}"
        timestamp = self.operations.get_remote_file_timestamp(remote_path)
        if timestamp is None:
            self.parent_window.toast_overlay.add_toast(
                Adw.Toast(title=_("Could not get remote file details."))
            )
            return

        transfer_id = self.transfer_manager.add_transfer(
            filename=file_item.name,
            local_path="",
            remote_path=remote_path,
            file_size=file_item.size,
            transfer_type="download",
            is_cancellable=True,
        )

        on_success_callback = partial(
            self._open_and_monitor_local_file,
            app_info=app_info,
            initial_timestamp=timestamp,
        )
        self.manager._start_cancellable_transfer(
            transfer_id, "Downloading", on_success_callback
        )

    def _open_and_monitor_local_file(
        self,
        local_path: Path,
        remote_path: str,
        app_info: Optional[Gio.AppInfo] = None,
        initial_timestamp: Optional[int] = None,
    ):
        self.manager._open_local_file(local_path, app_info)

        if remote_path in self.file_monitors:
            self.file_monitors[remote_path].cancel()

        local_gio_file = Gio.File.new_for_path(str(local_path))
        monitor = local_gio_file.monitor(Gio.FileMonitorFlags.NONE, None)
        monitor.connect("changed", self._on_local_file_saved, remote_path, local_path)
        self.file_monitors[remote_path] = monitor

        unique_dir_path = str(local_path.parent)
        self.edited_file_metadata[unique_dir_path] = {
            "remote_path": remote_path,
            "local_file_path": str(local_path),
            "timestamp": initial_timestamp,
        }
        self.manager.emit("temp-files-changed", len(self.edited_file_metadata))

        app = self.parent_window.get_application()
        if app:
            notification = Gio.Notification.new(_("Ashy Terminal"))
            notification.set_body(
                _("File is open. Saving it will upload changes back to the server.")
            )
            notification.set_icon(Gio.ThemedIcon.new("utilities-terminal-symbolic"))
            app.send_notification(f"ashy-file-open-{remote_path}", notification)

    def _on_local_file_saved(
        self, _monitor, _file, _other_file, event_type, remote_path, local_path
    ):
        if event_type == Gio.FileMonitorEvent.CHANGES_DONE_HINT:
            threading.Thread(
                target=self._check_conflict_and_upload,
                args=(local_path, remote_path),
                daemon=True,
            ).start()

    def _check_conflict_and_upload(self, local_path: Path, remote_path: str):
        unique_dir_path = str(local_path.parent)
        metadata = self.edited_file_metadata.get(unique_dir_path)
        if not metadata:
            return

        last_known_ts = metadata.get("timestamp")
        current_remote_ts = self.operations.get_remote_file_timestamp(remote_path)

        if current_remote_ts is None:
            self.logger.error(f"Could not verify remote timestamp for {remote_path}.")
            return

        if last_known_ts is not None and current_remote_ts > last_known_ts:
            self.logger.warning(f"Conflict detected for {remote_path}.")
            GLib.idle_add(self._show_conflict_dialog, local_path, remote_path)
        else:
            self._upload_on_save_thread(local_path, remote_path)

    def _show_conflict_dialog(self, local_path: Path, remote_path: str):
        dialog = Adw.MessageDialog(
            transient_for=self.parent_window,
            heading=_("File Conflict"),
            body=_(
                "The file '{filename}' has been modified on the server since you started editing it. How would you like to proceed?"
            ).format(filename=local_path.name),
            close_response="cancel",
        )
        dialog.add_response("cancel", _("Cancel Upload"))
        dialog.add_response("overwrite", _("Overwrite Server File"))
        dialog.add_response("save-as", _("Save as New File"))
        dialog.set_response_appearance("overwrite", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response("cancel")
        dialog.connect(
            "response",
            lambda d, res: self._on_conflict_response(
                res, local_path, remote_path, d
            ),
        )
        dialog.present()

    def _on_conflict_response(self, response_id, local_path, remote_path, dialog):
        dialog.close()
        if response_id == "overwrite":
            self._upload_on_save_thread(local_path, remote_path)
        elif response_id == "save-as":
            self._prompt_for_new_filename_and_upload(local_path, remote_path)

    def _prompt_for_new_filename_and_upload(self, local_path: Path, remote_path: str):
        dialog = Adw.MessageDialog(
            transient_for=self.parent_window,
            heading=_("Save As"),
            body=_("Enter a new name for the file on the server:"),
            close_response="cancel",
        )
        entry = Gtk.Entry(text=f"{local_path.stem}-copy{local_path.suffix}")
        dialog.set_extra_child(entry)
        dialog.add_response("cancel", _("Cancel"))
        dialog.add_response("save", _("Save"))
        dialog.set_default_response("save")
        dialog.connect(
            "response",
            lambda d, res: self._on_save_as_response(res, local_path, remote_path, d),
        )
        dialog.present()

    def _on_save_as_response(self, response_id, local_path, remote_path, dialog):
        dialog.close()
        if response_id == "save":
            new_name = dialog.get_extra_child().get_text().strip()
            if new_name:
                new_remote_path = str(Path(remote_path).parent / new_name)
                self._upload_on_save_thread(local_path, new_remote_path)

    def _upload_on_save_thread(self, local_path, remote_path):
        file_size = local_path.stat().st_size if local_path.exists() else 0
        transfer_id = self.transfer_manager.add_transfer(
            filename=local_path.name,
            local_path=str(local_path),
            remote_path=remote_path,
            file_size=file_size,
            transfer_type="upload",
            is_cancellable=True,
        )
        self.operations.start_upload_with_progress(
            transfer_id,
            self.manager.session_item,
            local_path,
            remote_path,
            progress_callback=self.transfer_manager.update_progress,
            completion_callback=self._on_save_upload_complete,
            cancellation_event=self.transfer_manager.get_cancellation_event(
                transfer_id
            ),
        )

    def _on_save_upload_complete(self, transfer_id, success, message):
        if success:
            self.transfer_manager.complete_transfer(transfer_id)
            transfer = next(
                (t for t in self.transfer_manager.history if t.id == transfer_id), None
            )
            if transfer:
                unique_dir_path = str(Path(transfer.local_path).parent)
                if unique_dir_path in self.edited_file_metadata:
                    new_ts = self.operations.get_remote_file_timestamp(
                        transfer.remote_path
                    )
                    if new_ts:
                        self.edited_file_metadata[unique_dir_path]["timestamp"] = new_ts
        else:
            self.transfer_manager.fail_transfer(transfer_id, message)

    def cleanup_temp_file_dir(self, dir_path_str: str):
        metadata = self.edited_file_metadata.pop(dir_path_str, None)
        if not metadata:
            return

        remote_path = metadata.get("remote_path")
        if remote_path:
            monitor = self.file_monitors.pop(remote_path, None)
            if monitor:
                monitor.cancel()

        try:
            shutil.rmtree(dir_path_str)
        except Exception as e:
            self.logger.error(f"Failed to remove temp dir {dir_path_str}: {e}")

        self.manager.emit("temp-files-changed", len(self.edited_file_metadata))
