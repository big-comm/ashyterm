# ashyterm/filemanager/transfer_dialog.py
import time
from datetime import datetime
from typing import Dict

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gtk

from ..utils.logger import get_logger
from ..utils.translation_utils import _
from .transfer_manager import TransferItem, TransferStatus, TransferType


class TransferRow(Gtk.ListBoxRow):
    """A row representing a single transfer with progress and controls."""

    def __init__(self, transfer: TransferItem, transfer_manager):
        super().__init__()
        self.transfer = transfer
        self.transfer_manager = transfer_manager
        self.logger = get_logger(__name__)

        self.set_activatable(False)
        self.set_selectable(False)

        self._build_ui()
        self.update_state()

    def _build_ui(self):
        """Build the transfer row UI."""
        main_box = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=6,
            margin_top=8,
            margin_bottom=8,
            margin_start=12,
            margin_end=12,
        )

        header_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)

        icon_name = (
            "folder-download-symbolic"
            if self.transfer.transfer_type == TransferType.DOWNLOAD
            else "folder-upload-symbolic"
        )
        self.type_icon = Gtk.Image.new_from_icon_name(icon_name)
        header_box.append(self.type_icon)

        info_box = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL, spacing=2, hexpand=True
        )
        self.filename_label = Gtk.Label(xalign=0.0, label=self.transfer.filename)
        self.filename_label.add_css_class("title-4")
        self.details_label = Gtk.Label(xalign=0.0)
        self.details_label.add_css_class("dim-label")
        info_box.append(self.filename_label)
        info_box.append(self.details_label)
        header_box.append(info_box)

        self.date_label = Gtk.Label(xalign=1.0)
        self.date_label.add_css_class("caption")
        header_box.append(self.date_label)

        self.action_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self.cancel_button = Gtk.Button.new_from_icon_name("process-stop-symbolic")
        self.cancel_button.set_tooltip_text(_("Cancel transfer"))
        self.cancel_button.add_css_class("flat")
        self.cancel_button.connect(
            "clicked", lambda _: self.transfer_manager.cancel_transfer(self.transfer.id)
        )

        self.retry_button = Gtk.Button.new_from_icon_name("view-refresh-symbolic")
        self.retry_button.set_tooltip_text(_("Retry transfer"))
        self.retry_button.add_css_class("flat")
        # self.retry_button.connect("clicked", self._on_retry_clicked) # TODO: Implement retry logic

        self.action_box.append(self.cancel_button)
        self.action_box.append(self.retry_button)
        header_box.append(self.action_box)

        main_box.append(header_box)

        self.progress_bar = Gtk.ProgressBar(hexpand=True)
        self.progress_bar.add_css_class("transfer-progress-bar")  # Add style class
        main_box.append(self.progress_bar)

        self.set_child(main_box)

    def update_state(self):
        """Update the display based on transfer status."""
        status = self.transfer.status
        size_str = self._format_file_size(self.transfer.file_size)
        type_str = (
            "Download"
            if self.transfer.transfer_type == TransferType.DOWNLOAD
            else "Upload"
        )
        self.date_label.set_text("")

        self.type_icon.remove_css_class("success")
        self.type_icon.remove_css_class("error")
        self.type_icon.remove_css_class("warning")

        if status == TransferStatus.PENDING:
            self.details_label.set_text(f"{type_str} • {size_str} • Waiting...")
            self.progress_bar.set_visible(False)
            self.cancel_button.set_visible(True)
            self.retry_button.set_visible(False)
        elif status == TransferStatus.IN_PROGRESS:
            self.progress_bar.set_visible(True)
            self.cancel_button.set_visible(True)
            self.retry_button.set_visible(False)
            self.update_progress()
        elif status == TransferStatus.COMPLETED:
            duration = self.transfer.get_duration()
            duration_str = f"in {self._format_duration(duration)}" if duration else ""
            self.details_label.set_text(
                f"{type_str} • {size_str} • Completed {duration_str}"
            )
            self.progress_bar.set_visible(False)
            self.cancel_button.set_visible(False)
            self.retry_button.set_visible(False)
            self.type_icon.add_css_class("success")
            if self.transfer.start_time:
                date_str = datetime.fromtimestamp(self.transfer.start_time).strftime(
                    "%Y-%m-%d %H:%M"
                )
                self.date_label.set_text(date_str)
        elif status == TransferStatus.FAILED:
            error_msg = self.transfer.error_message or "Unknown error"
            self.details_label.set_text(
                f"{type_str} • {size_str} • Failed: {error_msg}"
            )
            self.progress_bar.set_visible(False)
            self.cancel_button.set_visible(False)
            self.retry_button.set_visible(True)
            self.type_icon.add_css_class("error")
            if self.transfer.start_time:
                date_str = datetime.fromtimestamp(self.transfer.start_time).strftime(
                    "%Y-%m-%d %H:%M"
                )
                self.date_label.set_text(date_str)
        elif status == TransferStatus.CANCELLED:
            self.details_label.set_text(f"{type_str} • {size_str} • Cancelled")
            self.progress_bar.set_visible(False)
            self.cancel_button.set_visible(False)
            self.retry_button.set_visible(True)
            self.type_icon.add_css_class("warning")
            if self.transfer.start_time:
                date_str = datetime.fromtimestamp(self.transfer.start_time).strftime(
                    "%Y-%m-%d %H:%M"
                )
                self.date_label.set_text(date_str)

    def update_progress(self):
        """Update progress bar and details label for active transfers."""
        if self.transfer.status != TransferStatus.IN_PROGRESS:
            return

        progress = self.transfer.progress
        self.progress_bar.set_fraction(progress / 100.0)

        size_str = self._format_file_size(self.transfer.file_size)
        type_str = (
            "Download"
            if self.transfer.transfer_type == TransferType.DOWNLOAD
            else "Upload"
        )

        details_parts = [f"{type_str} • {size_str}", f"{progress:.1f}%"]

        if self.transfer.start_time:
            elapsed = time.time() - self.transfer.start_time
            if elapsed > 0.5 and self.transfer.file_size > 0:
                bytes_transferred = (progress / 100.0) * self.transfer.file_size
                speed = bytes_transferred / elapsed
                details_parts.append(f"{self._format_file_size(int(speed))}/s")

                if speed > 0:
                    remaining_bytes = self.transfer.file_size - bytes_transferred
                    eta_seconds = remaining_bytes / speed
                    details_parts.append(f"{self._format_duration(eta_seconds)} left")

        self.details_label.set_text(" • ".join(details_parts))

    def _format_file_size(self, size_bytes: int) -> str:
        if size_bytes == 0:
            return "0 B"
        sizes = ["B", "KB", "MB", "GB", "TB"]
        i = 0
        size_float = float(size_bytes)
        while size_float >= 1024 and i < len(sizes) - 1:
            size_float /= 1024.0
            i += 1
        return f"{size_float:.1f} {sizes[i]}"

    def _format_duration(self, seconds: float) -> str:
        if seconds < 60:
            return f"{int(seconds)}s"
        elif seconds < 3600:
            return f"{int(seconds // 60)}m {int(seconds % 60)}s"
        else:
            return f"{int(seconds // 3600)}h {int((seconds % 3600) // 60)}m"


class TransferManagerDialog(Adw.Window):
    def __init__(self, transfer_manager, parent_window):
        super().__init__(transient_for=parent_window)
        self.transfer_manager = transfer_manager
        self.logger = get_logger(__name__)
        self.transfer_rows: Dict[str, TransferRow] = {}

        self.set_title(_("Transfer Manager"))
        self.set_default_size(600, 500)
        self.set_modal(False)
        self.set_hide_on_close(True)

        self._build_ui()
        self._connect_signals()
        self._populate_transfers()
        self.connect("close-request", self._on_close_request)

    def _on_close_request(self, window):
        # Disconnect signals to prevent memory leaks from dangling references
        self.transfer_manager.disconnect_by_func(self._on_transfer_change)
        self.transfer_manager.disconnect_by_func(self._on_transfer_progress)
        return False  # Allow the window to close

    def _build_ui(self):
        toolbar_view = Adw.ToolbarView()
        self.set_content(toolbar_view)

        header = Adw.HeaderBar()
        toolbar_view.add_top_bar(header)

        clear_all_button = Gtk.Button(label=_("Clear History"))
        clear_all_button.add_css_class("destructive-action")
        clear_all_button.connect("clicked", self._on_clear_all_clicked)
        header.pack_start(clear_all_button)

        self.cancel_all_button = Gtk.Button(label=_("Cancel All"))
        self.cancel_all_button.connect("clicked", self._on_cancel_all_clicked)
        header.pack_end(self.cancel_all_button)

        scrolled = Gtk.ScrolledWindow(vexpand=True)
        scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)

        content_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.transfer_listbox = Gtk.ListBox()
        self.transfer_listbox.set_selection_mode(Gtk.SelectionMode.NONE)
        self.transfer_listbox.add_css_class("boxed-list")

        self.status_page = Adw.StatusPage(
            title=_("No Transfers"),
            description=_("Active and past transfers will appear here."),
            icon_name="folder-download-symbolic",
            vexpand=True,
            visible=False,
        )

        content_box.append(self.transfer_listbox)
        content_box.append(self.status_page)

        scrolled.set_child(content_box)
        toolbar_view.set_content(scrolled)

    def _update_view(self):
        has_transfers = (
            len(self.transfer_manager.active_transfers)
            + len(self.transfer_manager.history)
        ) > 0
        self.transfer_listbox.set_visible(has_transfers)
        self.status_page.set_visible(not has_transfers)

    def _connect_signals(self):
        self.transfer_manager.connect("transfer-started", self._on_transfer_change)
        self.transfer_manager.connect("transfer-progress", self._on_transfer_progress)
        self.transfer_manager.connect("transfer-completed", self._on_transfer_change)
        self.transfer_manager.connect("transfer-failed", self._on_transfer_change)
        self.transfer_manager.connect("transfer-cancelled", self._on_transfer_change)

    def _populate_transfers(self):
        all_transfers = (
            list(self.transfer_manager.active_transfers.values())
            + self.transfer_manager.history
        )
        all_transfers.sort(key=lambda t: t.start_time or time.time(), reverse=True)

        # By iterating through the newest-to-oldest list in reverse (i.e., oldest
        # to newest) and prepending each item, the final list in the UI will be
        # correctly ordered from newest to oldest.
        for transfer in reversed(all_transfers):
            self._add_or_update_row(transfer)

        self._update_cancel_all_button()
        self._update_view()

    def _add_or_update_row(self, transfer: TransferItem):
        if transfer.id in self.transfer_rows:
            row = self.transfer_rows[transfer.id]
            row.transfer = transfer
            row.update_state()
        else:
            row = TransferRow(transfer, self.transfer_manager)
            self.transfer_rows[transfer.id] = row
            # Always prepend to keep the newest at the top
            self.transfer_listbox.prepend(row)

    def _on_transfer_change(self, manager, transfer_id, *_):
        transfer = manager.get_transfer(transfer_id) or next(
            (t for t in manager.history if t.id == transfer_id), None
        )
        if transfer:
            GLib.idle_add(self._add_or_update_row, transfer)
        GLib.idle_add(self._update_cancel_all_button)
        GLib.idle_add(self._update_view)

    def _on_transfer_progress(self, manager, transfer_id, progress):
        if transfer_id in self.transfer_rows:
            row = self.transfer_rows[transfer_id]
            # Ensure transfer object on row is up-to-date
            transfer_obj = manager.get_transfer(transfer_id)
            if transfer_obj:
                row.transfer = transfer_obj
                GLib.idle_add(row.update_progress)

    def _update_cancel_all_button(self):
        has_active = len(self.transfer_manager.active_transfers) > 0
        self.cancel_all_button.set_visible(has_active)

    def _on_cancel_all_clicked(self, button):
        for transfer_id in list(self.transfer_manager.active_transfers.keys()):
            self.transfer_manager.cancel_transfer(transfer_id)

    def _on_clear_all_clicked(self, button):
        dialog = Adw.AlertDialog(
            heading=_("Clear Transfer History?"),
            body=_(
                "This action cannot be undone and will remove all completed, failed, and cancelled transfers from the list."
            ),
            default_response="cancel",
            close_response="cancel",
        )
        dialog.add_response("cancel", _("Cancel"))
        dialog.add_response("clear", _("Clear History"))
        dialog.set_response_appearance("clear", Adw.ResponseAppearance.DESTRUCTIVE)

        dialog.connect("response", self._on_clear_confirm)
        dialog.present(self)

    def _on_clear_confirm(self, dialog, response):
        if response == "clear":
            ids_to_remove = [
                tid
                for tid, row in self.transfer_rows.items()
                if row.transfer.status
                not in [TransferStatus.IN_PROGRESS, TransferStatus.PENDING]
            ]

            for transfer_id in ids_to_remove:
                row = self.transfer_rows.pop(transfer_id)
                self.transfer_listbox.remove(row)

            self.transfer_manager.history = [
                t for t in self.transfer_manager.history if t.id not in ids_to_remove
            ]
            self.transfer_manager._save_history()
            self._update_view()
