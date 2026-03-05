# ashyterm/filemanager/transfers.py
"""File transfer mixin: download, upload, drag-drop, workers, edit/monitor/conflict."""

import subprocess
import threading
from functools import partial
from pathlib import Path
from typing import List, Optional

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gio, GLib, Gtk

from ..sessions.models import SessionItem
from ..utils.security import InputSanitizer
from ..utils.translation_utils import _
from .models import FileItem
from .transfer_dialog import TransferManagerDialog
from .transfer_manager import TransferType


class FileTransferMixin:
    """Mixin providing file transfer, edit, and monitoring functionality."""

    def _on_download_action(self, _action, _param, items: List[FileItem]):
        dialog = Gtk.FileDialog(
            title=_("Select Destination Folder"),
            modal=True,
            accept_label=_("Download Here"),
        )

        # Remember last download folder
        last_folder = getattr(self, "_last_download_folder", None)
        if not last_folder and hasattr(self, "settings_manager") and self.settings_manager:
            last_folder = self.settings_manager.get("last_download_folder", "")
        if last_folder:
            folder_file = Gio.File.new_for_path(last_folder)
            if folder_file.query_exists():
                dialog.set_initial_folder(folder_file)

        dialog.select_folder(
            self.parent_window, None, self._on_download_dialog_response, items
        )

    def _on_download_dialog_response(self, source, result, items: List[FileItem]):
        try:
            dest_folder = source.select_folder_finish(result)
            if not dest_folder:
                return

            dest_path = Path(dest_folder.get_path())

            # Persist selected folder for next time
            self._last_download_folder = str(dest_path)
            if hasattr(self, "settings_manager") and self.settings_manager:
                self.settings_manager.set("last_download_folder", str(dest_path))

            threading.Thread(
                target=self._prepare_and_start_downloads,
                args=(items, dest_path),
                daemon=True,
            ).start()

        except GLib.Error as e:
            if not e.matches(Gio.io_error_quark(), Gio.IOErrorEnum.CANCELLED):
                self.logger.error(f"File chooser error: {e.message}")
                self.parent_window._show_error_dialog(
                    _("Error"), _("Could not open file chooser.")
                )

    def _prepare_and_start_downloads(
        self, items: List[FileItem], dest_path: Path
    ) -> None:
        """Prepare downloads in background and start them if space permits."""
        try:
            item_sizes = self._calculate_item_sizes(items)
            total_size_needed = sum(item_sizes.values())
            free_space = self.operations.get_free_space(str(dest_path), is_remote=False)

            if free_space > 0 and total_size_needed > free_space:
                GLib.idle_add(
                    self._show_insufficient_space_dialog,
                    total_size_needed,
                    free_space,
                    dest_path,
                )
                return

            GLib.idle_add(self._execute_downloads, items, item_sizes, dest_path)

        except Exception as e:
            self.logger.error(f"Error preparing downloads: {e}")
            GLib.idle_add(
                self.parent_window._show_error_dialog,
                _("Download Error"),
                _("Failed to prepare download. Check your connection and try again."),
            )

    def _calculate_item_sizes(self, items: List[FileItem]) -> dict:
        """Calculate the actual size of each item for download."""
        item_sizes = {}
        for item in items:
            remote_path = f"{self.current_path.rstrip('/')}/{item.name}"

            if item.is_directory_like or item.size == 0:
                calculated_size = self.operations.get_directory_size(
                    remote_path, is_remote=True, session_override=self.session_item
                )
                item_sizes[item.name] = (
                    calculated_size if calculated_size > 0 else item.size
                )
            else:
                item_sizes[item.name] = item.size

        return item_sizes

    def _execute_downloads(
        self, items: List[FileItem], item_sizes: dict, dest_path: Path
    ) -> bool:
        """Start the actual download transfers."""
        for item in items:
            file_size = item_sizes.get(item.name, item.size)
            transfer_id = self.transfer_manager.add_transfer(
                filename=item.name,
                local_path=str(dest_path / item.name),
                remote_path=f"{self.current_path.rstrip('/')}/{item.name}",
                file_size=file_size,
                transfer_type=TransferType.DOWNLOAD,
                is_cancellable=True,
                is_directory=item.is_directory_like,
            )
            self._start_cancellable_transfer(
                transfer_id,
                "Downloading",
                self._background_download_worker,
                on_success_callback=self._on_download_success,
            )
        return False

    def _on_download_success(self, local_path, remote_path) -> None:
        """Refresh view if download was to the current local directory."""
        if self._is_remote_session():
            return

        current_resolved = Path(self.current_path).resolve()
        download_parent = Path(local_path).parent.resolve()

        if current_resolved == download_parent:
            self.logger.info(
                "Download to current local directory completed. Refreshing view."
            )
            self.refresh(source="filemanager")

    def _on_upload_action(self, _action, _param, _file_item: Optional[FileItem]):
        dialog = Gtk.FileDialog(
            title=_("Upload File(s) to This Folder"),
            modal=True,
            accept_label=_("Upload"),
        )

        # Remember last upload folder
        last_folder = getattr(self, "_last_upload_folder", None)
        if not last_folder and hasattr(self, "settings_manager") and self.settings_manager:
            last_folder = self.settings_manager.get("last_upload_folder", "")
        if last_folder:
            folder_file = Gio.File.new_for_path(last_folder)
            if folder_file.query_exists():
                dialog.set_initial_folder(folder_file)

        dialog.open_multiple(self.parent_window, None, self._on_upload_dialog_response)

    def _on_upload_dialog_response(self, source, result):
        try:
            files = source.open_multiple_finish(result)
            if files:
                local_paths = [Path(gio_file.get_path()) for gio_file in files]

                # Persist selected folder for next time
                first_parent = str(local_paths[0].parent)
                self._last_upload_folder = first_parent
                if hasattr(self, "settings_manager") and self.settings_manager:
                    self.settings_manager.set("last_upload_folder", first_parent)

                self._prepare_and_start_uploads(local_paths)
        except GLib.Error as e:
            if not e.matches(Gio.io_error_quark(), Gio.IOErrorEnum.CANCELLED):
                self.logger.error(f"File chooser error: {e.message}")
                self.parent_window._show_error_dialog(
                    _("Error"), _("Could not open file chooser.")
                )

    def _initiate_upload(self, local_path: Path):
        """Helper to start the upload process for a single local path."""
        # For backward compatibility, calculate size here
        if local_path.is_dir():
            file_size = self.operations.get_directory_size(
                str(local_path), is_remote=False
            )
        else:
            file_size = local_path.stat().st_size if local_path.exists() else 0
        self._initiate_upload_with_size(local_path, file_size)

    def _initiate_upload_with_size(self, local_path: Path, file_size: int):
        """Helper to start the upload process with pre-calculated size."""
        remote_path = f"{self.current_path.rstrip('/')}/{local_path.name}"
        transfer_id = self.transfer_manager.add_transfer(
            filename=local_path.name,
            local_path=str(local_path),
            remote_path=remote_path,
            file_size=file_size,
            transfer_type=TransferType.UPLOAD,
            is_cancellable=True,
            is_directory=local_path.is_dir(),
        )
        self._start_cancellable_transfer(
            transfer_id,
            "Uploading",
            self._background_upload_worker,
            on_success_callback=lambda _, __: GLib.idle_add(
                lambda: self.refresh(source="filemanager")
            ),
        )

    def _calculate_local_paths_size(
        self, local_paths: list[Path]
    ) -> tuple[int, dict[str, int]]:
        """Calculate total size and individual sizes for local paths.

        This helper function extracts the common logic for calculating
        sizes of local files/directories before upload.

        Args:
            local_paths: List of Path objects to calculate sizes for.

        Returns:
            Tuple of (total_size_needed, path_sizes_dict)
        """
        total_size_needed = 0
        path_sizes: dict[str, int] = {}

        for local_path in local_paths:
            if local_path.is_dir():
                size = self.operations.get_directory_size(
                    str(local_path), is_remote=False
                )
            else:
                size = local_path.stat().st_size if local_path.exists() else 0

            path_sizes[str(local_path)] = size
            total_size_needed += size

        return total_size_needed, path_sizes

    def _prepare_and_start_uploads(self, local_paths: list[Path]) -> None:
        """Prepare uploads by checking space and start the upload process.

        This helper function extracts the common logic for preparing and
        starting uploads that was duplicated in _on_upload_dialog_response
        and _on_upload_confirmation_response.

        Args:
            local_paths: List of Path objects to upload.
        """

        def prepare_uploads():
            try:
                total_size_needed, path_sizes = self._calculate_local_paths_size(
                    local_paths
                )

                # Check available space at remote destination
                free_space = self.operations.get_free_space(
                    self.current_path,
                    is_remote=True,
                    session_override=self.session_item,
                )

                if free_space > 0 and total_size_needed > free_space:

                    def show_space_error():
                        self._show_insufficient_space_dialog(
                            total_size_needed, free_space, Path(self.current_path)
                        )
                        return False

                    GLib.idle_add(show_space_error)
                    return

                # Start uploads on main thread
                def start_uploads():
                    for local_path in local_paths:
                        file_size = path_sizes.get(str(local_path), 0)
                        self._initiate_upload_with_size(local_path, file_size)
                    return False

                GLib.idle_add(start_uploads)

            except Exception as e:
                self.logger.error(f"Error preparing uploads: {e}")

                def show_error():
                    self.parent_window._show_error_dialog(
                        _("Upload Error"),
                        _(
                            "Failed to prepare upload. Check your connection and try again."
                        ),
                    )
                    return False

                GLib.idle_add(show_error)

        threading.Thread(target=prepare_uploads, daemon=True).start()

    def _on_upload_clicked(self, button):
        self._on_upload_action(None, None, None)

    def _on_transfer_history_destroyed(self, widget):
        self.transfer_history_window = None

    def _on_show_transfer_history(self, button):
        if self.transfer_history_window:
            self.transfer_history_window.present()
            return

        self.transfer_history_window = TransferManagerDialog(
            self.transfer_manager, self.parent_window
        )
        self.transfer_history_window.connect(
            "destroy", self._on_transfer_history_destroyed
        )
        self.transfer_history_window.present()

    def _on_drop_accept(self, target, _drop):
        return self._is_remote_session()

    def _on_drop_enter(self, target, x, y, scrolled_window):
        scrolled_window.add_css_class("drop-target")
        return Gdk.DragAction.COPY

    def _on_drop_leave(self, target, scrolled_window):
        scrolled_window.remove_css_class("drop-target")

    def _on_files_dropped(self, drop_target, value, x, y, scrolled_window):
        scrolled_window.remove_css_class("drop-target")
        if not self._is_remote_session():
            return False

        files_to_upload = []
        if isinstance(value, Gdk.FileList):
            for file in value.get_files():
                if path_str := file.get_path():
                    files_to_upload.append(Path(path_str))

        if files_to_upload:
            self._show_upload_confirmation_dialog(files_to_upload)

        return True

    def _show_upload_confirmation_dialog(self, local_paths: List[Path]):
        count = len(local_paths)
        dialog = Adw.MessageDialog(
            transient_for=self.parent_window,
            heading=_("Confirm Upload"),
            body=_(
                "You are about to upload {count} item(s) to:\n<b>{dest}</b>\n\nDo you want to proceed?"
            ).format(count=count, dest=self.current_path),
            body_use_markup=True,
            close_response="cancel",
        )

        scrolled_window = Gtk.ScrolledWindow(
            vexpand=True, min_content_height=100, max_content_height=200
        )
        list_box = Gtk.ListBox(css_classes=["boxed-list"])
        scrolled_window.set_child(list_box)

        for path in local_paths:
            list_box.append(Gtk.Label(label=path.name, xalign=0.0))

        dialog.set_extra_child(scrolled_window)
        dialog.add_response("cancel", _("Cancel"))
        dialog.add_response("upload", _("Upload"))
        dialog.set_response_appearance("upload", Adw.ResponseAppearance.SUGGESTED)
        dialog.set_default_response("upload")
        dialog.connect("response", self._on_upload_confirmation_response, local_paths)
        dialog.present()

    def _on_upload_confirmation_response(self, dialog, response_id, local_paths):
        if response_id == "upload":
            self._prepare_and_start_uploads(local_paths)

    def _get_local_path_for_remote_file(
        self, session: SessionItem, remote_path: str
    ) -> Path:
        """Constructs a deterministic, human-readable local path for a remote file."""
        sanitized_session_name = InputSanitizer.sanitize_filename(session.name).replace(
            " ", "_"
        )
        # Remove leading slash from remote_path to prevent it being treated as an absolute path
        clean_remote_path = remote_path.lstrip("/")
        local_path = self.remote_edit_dir / sanitized_session_name / clean_remote_path
        local_path.parent.mkdir(parents=True, exist_ok=True)
        return local_path

    def _on_open_edit_action(self, _action, _param, items: List[FileItem]):
        if not items:
            return
        file_item = items[0]  # Open/Edit only works on single items

        if not self._is_remote_session():
            full_path = Path(self.current_path).joinpath(file_item.name)
            self._open_local_file(full_path)
            return

        remote_path = f"{self.current_path.rstrip('/')}/{file_item.name}"
        edit_key = (self.session_item.name, remote_path)

        if edit_key in self.edited_file_metadata:
            metadata = self.edited_file_metadata[edit_key]
            local_path = Path(metadata["local_file_path"])
            last_known_ts = metadata["timestamp"]

            current_remote_ts = self.operations.get_remote_file_timestamp(remote_path)

            if (
                current_remote_ts
                and last_known_ts
                and current_remote_ts > last_known_ts
            ):
                self._show_conflict_on_open_dialog(local_path, remote_path, file_item)
            else:
                self.logger.info(f"Opening existing local copy for {remote_path}")
                self._open_local_file(local_path)
        else:
            self._download_and_execute(file_item, self._open_and_monitor_local_file)

    def _show_conflict_on_open_dialog(self, local_path, remote_path, file_item):
        dialog = Adw.MessageDialog(
            transient_for=self.parent_window,
            heading=_("File Has Changed on Server"),
            body=_(
                "The file '{filename}' has been modified on the server since you last opened it. Your local changes will be lost if you download the new version."
            ).format(filename=file_item.name),
            close_response="cancel",
        )
        dialog.add_response("cancel", _("Cancel"))
        dialog.add_response("open-local", _("Open Local Version"))
        dialog.add_response("download-new", _("Download New Version"))
        dialog.set_response_appearance(
            "download-new", Adw.ResponseAppearance.DESTRUCTIVE
        )

        def on_response(d, response_id):
            if response_id == "open-local":
                self._open_local_file(local_path)
            elif response_id == "download-new":
                self._download_and_execute(file_item, self._open_and_monitor_local_file)
            d.close()

        dialog.connect("response", on_response)
        dialog.present()

    def _on_open_with_action(self, _action, _param, items: List[FileItem]):
        if not items:
            return
        file_item = items[0]  # Open With only works on single items

        if self._is_remote_session():
            self._download_and_execute(file_item, self._show_open_with_dialog)
        else:
            full_path = Path(self.current_path).joinpath(file_item.name)
            self._show_open_with_dialog(full_path, remote_path=None)

    def _download_and_execute(self, file_item: FileItem, on_success_callback):
        remote_path = f"{self.current_path.rstrip('/')}/{file_item.name}"
        timestamp = self.operations.get_remote_file_timestamp(remote_path)
        if timestamp is None:
            self.parent_window.toast_overlay.add_toast(
                Adw.Toast(title=_("Could not get remote file details."))
            )
            return

        local_path = self._get_local_path_for_remote_file(
            self.session_item, remote_path
        )

        transfer_id = self.transfer_manager.add_transfer(
            filename=file_item.name,
            local_path=str(local_path),
            remote_path=remote_path,
            file_size=file_item.size,
            transfer_type=TransferType.DOWNLOAD,
            is_cancellable=True,
            is_directory=file_item.is_directory_like,
        )
        success_callback_with_ts = partial(
            on_success_callback, initial_timestamp=timestamp
        )
        self._start_cancellable_transfer(
            transfer_id,
            "Downloading",
            self._background_download_worker,
            success_callback_with_ts,
        )

    def _start_cancellable_transfer(
        self, transfer_id, _verb, worker_func, on_success_callback
    ):
        transfer = self.transfer_manager.get_transfer(transfer_id)
        if not transfer:
            return

        thread = threading.Thread(
            target=worker_func, args=(transfer_id, on_success_callback), daemon=True
        )
        thread.start()

    def _background_download_worker(self, transfer_id, on_success_callback):
        transfer = self.transfer_manager.get_transfer(transfer_id)
        if not transfer:
            return

        try:
            self.transfer_manager.start_transfer(transfer_id)
            completion_callback = partial(
                self._on_transfer_complete, on_success_callback
            )
            self.operations.start_download_with_progress(
                transfer_id,
                self.session_item,
                transfer.remote_path,
                Path(transfer.local_path),
                is_directory=transfer.is_directory,
                progress_callback=self.transfer_manager.update_progress,
                completion_callback=completion_callback,
                cancellation_event=self.transfer_manager.get_cancellation_event(
                    transfer_id
                ),
            )
        except Exception as e:
            GLib.idle_add(
                self._on_transfer_complete,
                on_success_callback,
                transfer_id,
                False,
                str(e),
            )

    def _background_upload_worker(self, transfer_id, on_success_callback):
        transfer = self.transfer_manager.get_transfer(transfer_id)
        if not transfer:
            return

        try:
            self.transfer_manager.start_transfer(transfer_id)
            completion_callback = partial(
                self._on_transfer_complete, on_success_callback
            )
            self.operations.start_upload_with_progress(
                transfer_id,
                self.session_item,
                Path(transfer.local_path),
                transfer.remote_path,
                is_directory=transfer.is_directory,
                progress_callback=self.transfer_manager.update_progress,
                completion_callback=completion_callback,
                cancellation_event=self.transfer_manager.get_cancellation_event(
                    transfer_id
                ),
            )
        except Exception as e:
            GLib.idle_add(
                self._on_transfer_complete,
                on_success_callback,
                transfer_id,
                False,
                str(e),
            )

    def _on_transfer_complete(self, on_success_callback, transfer_id, success, message):
        if success:
            self.transfer_manager.complete_transfer(transfer_id)
            if on_success_callback:
                transfer = self.transfer_manager.history[0]
                if transfer:
                    on_success_callback(Path(transfer.local_path), transfer.remote_path)
        else:
            permission_denied_key = _("Permission Denied")
            if permission_denied_key in message:
                self._show_permission_error_dialog(transfer_id, message)

            self.transfer_manager.fail_transfer(transfer_id, message)
            if message == "Cancelled":
                self.parent_window.toast_overlay.add_toast(
                    Adw.Toast(title=_("Transfer cancelled."))
                )

    def _show_insufficient_space_dialog(
        self, required_bytes: int, available_bytes: int, dest_path: Path
    ):
        """Shows a dialog when there's not enough space for the transfer."""

        def format_size(size_bytes: int) -> str:
            if size_bytes < 1024:
                return f"{size_bytes} B"
            elif size_bytes < 1024 * 1024:
                return f"{size_bytes / 1024:.1f} KB"
            elif size_bytes < 1024 * 1024 * 1024:
                return f"{size_bytes / (1024 * 1024):.1f} MB"
            else:
                return f"{size_bytes / (1024 * 1024 * 1024):.2f} GB"

        dialog = Adw.MessageDialog(
            transient_for=self.parent_window,
            heading=_("Insufficient Disk Space"),
            body=_(
                "There is not enough free space at the destination to complete this transfer."
            ),
            close_response="ok",
        )

        details = _(
            "Required space: <b>{required}</b>\n"
            "Available space: <b>{available}</b>\n"
            "Destination: <b>{path}</b>"
        ).format(
            required=format_size(required_bytes),
            available=format_size(available_bytes),
            path=str(dest_path),
        )
        dialog.set_extra_child(
            Gtk.Label(label=details, use_markup=True, wrap=True, xalign=0)
        )
        dialog.add_response("ok", _("OK"))
        dialog.present()

    def _show_permission_error_dialog(self, transfer_id: str, message: str):
        """Shows a specific dialog for permission errors."""
        transfer = self.transfer_manager.get_transfer(transfer_id) or next(
            (t for t in self.transfer_manager.history if t.id == transfer_id), None
        )
        if not transfer:
            return

        dialog = Adw.MessageDialog(
            transient_for=self.parent_window,
            heading=_("Transfer Failed: Permission Denied"),
            body=_("Could not complete the transfer of '{filename}'.").format(
                filename=transfer.filename
            ),
            close_response="ok",
        )
        details = _(
            "Please check if you have the necessary write permissions in the destination directory:\n\n<b>{path}</b>"
        ).format(
            path=(
                transfer.remote_path
                if transfer.transfer_type == TransferType.UPLOAD
                else transfer.local_path
            )
        )
        dialog.set_extra_child(Gtk.Label(label=details, use_markup=True, wrap=True))
        dialog.add_response("ok", _("OK"))
        dialog.present()

    def _show_open_with_dialog(
        self,
        local_path: Path,
        remote_path: Optional[str] = None,
        initial_timestamp: Optional[int] = None,
    ):
        try:
            local_gio_file = Gio.File.new_for_path(str(local_path))
            dialog = Gtk.AppChooserDialog.new(
                self.parent_window, Gtk.DialogFlags.MODAL, local_gio_file
            )
            dialog.set_default_size(550, 450)
            dialog.set_title(_("Open With..."))

            def on_response(d, response_id):
                if response_id == Gtk.ResponseType.OK:
                    app_info = d.get_app_info()
                    if app_info:
                        if remote_path:
                            self._open_and_monitor_local_file(
                                local_path, remote_path, app_info, initial_timestamp
                            )
                            self._open_local_file(local_path, app_info)
                # Defer destruction to avoid segfaults in Wayland callbacks
                GLib.idle_add(d.destroy)

            dialog.connect("response", on_response)
            dialog.present()
        except Exception as e:
            self.logger.error(f"Failed to show 'Open With' dialog: {e}")
        return False

    def _open_local_file(self, local_path: Path, app_info: Gio.AppInfo = None):
        """Opens a local file with a specific app or the default."""
        local_gio_file = Gio.File.new_for_path(str(local_path))

        if not app_info:
            try:
                content_type = Gio.content_type_guess(str(local_path), None)[0]
                app_info = Gio.AppInfo.get_default_for_type(content_type, False)
            except Exception as e:
                self.logger.warning(
                    f"Could not find default app info for {local_path}: {e}"
                )
                app_info = None
        try:
            if app_info:
                app_info.launch([local_gio_file], None)
            else:
                subprocess.Popen(["xdg-open", str(local_path)])
        except Exception as e:
            self.logger.error(f"Failed to open local file {local_path}: {e}")
            self.parent_window.toast_overlay.add_toast(
                Adw.Toast(
                    title=_("Failed to open file: {}").format(
                        local_path.name if hasattr(local_path, "name") else local_path
                    )
                )
            )

    def _open_and_monitor_local_file(
        self,
        local_path: Path,
        remote_path: str,
        app_info: Gio.AppInfo = None,
        initial_timestamp: Optional[int] = None,
    ):
        local_gio_file = Gio.File.new_for_path(str(local_path))

        if not app_info:
            content_type = Gio.content_type_guess(str(local_path), None)[0]
            app_info = Gio.AppInfo.get_default_for_type(content_type, False)

        if app_info:
            app_info.launch([local_gio_file], None)
        else:
            subprocess.Popen(["xdg-open", str(local_path)])

        edit_key = (self.session_item.name, remote_path)

        if edit_key in self.file_monitors:
            self.file_monitors[edit_key].cancel()

        monitor = local_gio_file.monitor(Gio.FileMonitorFlags.NONE, None)
        monitor.connect("changed", self._on_local_file_saved, remote_path, local_path)
        self.file_monitors[edit_key] = monitor

        self.edited_file_metadata[edit_key] = {
            "session_name": self.session_item.name,
            "remote_path": remote_path,
            "local_file_path": str(local_path),
            "timestamp": initial_timestamp,
        }
        self.emit("temp-files-changed", len(self.edited_file_metadata))

        app = self.parent_window.get_application()
        if app:
            notification = Gio.Notification.new(_("Ashy Terminal"))
            notification.set_body(
                _("File is open. Saving it will upload changes back to the server.")
            )
            notification.set_icon(Gio.ThemedIcon.new("utilities-terminal-symbolic"))
            app.send_notification(f"ashy-file-open-{remote_path}", notification)

        return False

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
        """Checks for remote changes before uploading the local file."""
        edit_key = (self.session_item.name, remote_path)
        metadata = self.edited_file_metadata.get(edit_key)
        if not metadata:
            self.logger.warning(
                f"No metadata for edited file {local_path}, cannot upload."
            )
            return

        last_known_timestamp = metadata.get("timestamp")
        current_remote_timestamp = self.operations.get_remote_file_timestamp(
            remote_path
        )

        if current_remote_timestamp is None:
            self.logger.error(
                f"Could not verify remote timestamp for {remote_path}. Aborting upload."
            )
            GLib.idle_add(
                self.parent_window.toast_overlay.add_toast,
                Adw.Toast(title=_("Upload failed: Could not verify remote file.")),
            )
            return

        if (
            last_known_timestamp is not None
            and current_remote_timestamp > last_known_timestamp
        ):
            self.logger.warning(f"Conflict detected for {remote_path}. Prompting user.")
            GLib.idle_add(self._show_conflict_dialog, local_path, remote_path)
        else:
            self.logger.info(f"No conflict for {remote_path}. Proceeding with upload.")
            self._upload_on_save_thread(local_path, remote_path)

    def _show_conflict_dialog(self, local_path: Path, remote_path: str):
        """Shows a dialog to the user to resolve an edit conflict."""
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

        def on_response(d, response_id):
            if response_id == "overwrite":
                self._upload_on_save_thread(local_path, remote_path)
            elif response_id == "save-as":
                self._prompt_for_new_filename_and_upload(local_path, remote_path)
            d.close()

        dialog.connect("response", on_response)
        dialog.present()

    def _prompt_for_new_filename_and_upload(self, local_path: Path, remote_path: str):
        """Prompts for a new filename and uploads the file."""
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

        def on_response(d, response_id):
            if response_id == "save":
                new_name = entry.get_text().strip()
                if new_name:
                    new_remote_path = str(Path(remote_path).parent / new_name)
                    self._upload_on_save_thread(local_path, new_remote_path)
            d.close()

        dialog.connect("response", on_response)
        dialog.present()

    def _on_save_upload_complete(self, transfer_id, success, message):
        """Callback to finalize transfer and show system notification."""
        if success:
            self.transfer_manager.complete_transfer(transfer_id)
            transfer = next(
                (t for t in self.transfer_manager.history if t.id == transfer_id), None
            )
            if transfer:
                edit_key = (self.session_item.name, transfer.remote_path)
                if edit_key in self.edited_file_metadata:
                    new_ts = self.operations.get_remote_file_timestamp(
                        transfer.remote_path
                    )
                    if new_ts:
                        self.edited_file_metadata[edit_key]["timestamp"] = new_ts
        else:
            self.transfer_manager.fail_transfer(transfer_id, message)

        app = self.parent_window.get_application()
        if not app:
            return

        transfer = next(
            (t for t in self.transfer_manager.history if t.id == transfer_id), None
        )
        if not transfer:
            return

        notification = Gio.Notification.new(_("Ashy Terminal"))
        if success:
            notification.set_title(_("Upload Complete"))
            notification.set_body(
                _("'{filename}' has been saved to the server.").format(
                    filename=transfer.filename
                )
            )
        else:
            notification.set_title(_("Upload Failed"))
            notification.set_body(
                _("Could not save '{filename}' to the server: {error}").format(
                    filename=transfer.filename, error=message
                )
            )
        notification.set_icon(Gio.ThemedIcon.new("utilities-terminal-symbolic"))
        app.send_notification(f"ashy-upload-complete-{transfer_id}", notification)

    def _upload_on_save_thread(self, local_path, remote_path):
        """Handles uploading a file on save using the TransferManager."""
        try:
            file_size = local_path.stat().st_size if local_path.exists() else 0
            transfer_id = self.transfer_manager.add_transfer(
                filename=local_path.name,
                local_path=str(local_path),
                remote_path=remote_path,
                file_size=file_size,
                transfer_type=TransferType.UPLOAD,
                is_cancellable=True,
                is_directory=local_path.is_dir(),
            )
            self.operations.start_upload_with_progress(
                transfer_id,
                self.session_item,
                local_path,
                remote_path,
                is_directory=local_path.is_dir(),
                progress_callback=self.transfer_manager.update_progress,
                completion_callback=self._on_save_upload_complete,
                cancellation_event=self.transfer_manager.get_cancellation_event(
                    transfer_id
                ),
            )
        except Exception as e:
            self.logger.error(f"Failed to initiate upload-on-save: {e}")

    def _on_rename_action(self, _action, _param, items: List[FileItem]):
        if not items or len(items) > 1:
            return
        file_item = items[0]
        dialog = Adw.AlertDialog(
            heading=_("Rename"),
            body=_("Enter a new name for '{name}'").format(name=file_item.name),
            close_response="cancel",
        )
        entry = Gtk.Entry(text=file_item.name, hexpand=True, activates_default=True)
        entry.select_region(0, -1)
        dialog.set_extra_child(entry)
        dialog.add_response("cancel", _("Cancel"))
        dialog.add_response("rename", _("Rename"))
        dialog.set_response_appearance("rename", Adw.ResponseAppearance.SUGGESTED)
        dialog.set_default_response("rename")
        dialog.connect("response", self._on_rename_dialog_response, file_item, entry)
        dialog.present(self.parent_window)

    def _on_rename_dialog_response(self, dialog, response, file_item, entry):
        if response == "rename":
            new_name = entry.get_text().strip()
            if new_name and new_name != file_item.name:
                old_path = f"{self.current_path.rstrip('/')}/{file_item.name}"
                new_path = f"{self.current_path.rstrip('/')}/{new_name}"
                command = ["mv", old_path, new_path]
                self._execute_verified_command(command, command_type="mv")
                self.parent_window.toast_overlay.add_toast(
                    Adw.Toast(title=_("Rename command sent to terminal"))
                )

    def _cleanup_edited_file(self, edit_key: tuple):
        """Cleans up all resources associated with a closed temporary file.

        Returns False for GLib.idle_add callback compatibility.
        """
        metadata = self.edited_file_metadata.pop(edit_key, None)
        if not metadata:
            return False

        monitor = self.file_monitors.pop(edit_key, None)
        if monitor:
            monitor.cancel()

        try:
            local_path = Path(metadata["local_file_path"])
            if local_path.exists():
                local_path.unlink()
                self.logger.info(f"Removed temporary file: {local_path}")
                # Clean up empty parent directories
                try:
                    parent = local_path.parent
                    while parent != self.remote_edit_dir and not any(parent.iterdir()):
                        parent.rmdir()
                        parent = parent.parent
                except OSError as e:
                    self.logger.warning(f"Could not remove empty parent dir: {e}")
        except Exception as e:
            self.logger.error(
                f"Failed to remove temporary file for key {edit_key}: {e}"
            )

        self.emit("temp-files-changed", len(self.edited_file_metadata))
        return False
