# ashyterm/filemanager/transfer_dialog.py
import time
from datetime import datetime
from typing import Callable, Dict

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, GObject, Gtk

from ..utils.icons import icon_button, icon_image
from ..utils.logger import get_logger
from ..utils.tooltip_helper import get_tooltip_helper
from ..utils.translation_utils import _
from .transfer_manager import TransferItem, TransferStatus, TransferType


class TransferRow(Adw.ActionRow):
    """A row representing a single transfer, using Adw.ActionRow for consistency."""

    def __init__(
        self,
        transfer: TransferItem,
        transfer_manager,
        on_remove_callback: Callable[[str], None],
    ):
        super().__init__()
        self.transfer = transfer
        self.transfer_manager = transfer_manager
        self.on_remove_callback = on_remove_callback
        self.logger = get_logger(__name__)

        self._build_ui()
        self.update_state()

    def _build_ui(self):
        """Build the transfer row UI within the Adw.ActionRow."""
        self.set_activatable(False)

        # Improved icon for transfer type
        icon_name = (
            "go-down-symbolic"
            if self.transfer.transfer_type == TransferType.DOWNLOAD
            else "go-up-symbolic"
        )
        self.type_icon = icon_image(icon_name)
        self.add_prefix(self.type_icon)

        # Progress bar as a suffix, managed for visibility
        self.progress_bar = Gtk.ProgressBar(hexpand=True, margin_end=12)
        self.progress_bar.add_css_class("transfer-progress-bar")
        self.add_suffix(self.progress_bar)

        # Action buttons
        self.action_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self.cancel_button = icon_button("process-stop-symbolic")
        get_tooltip_helper().add_tooltip(self.cancel_button, _("Cancel transfer"))
        self.cancel_button.add_css_class("flat")
        self.cancel_button.connect(
            "clicked", lambda _: self.transfer_manager.cancel_transfer(self.transfer.id)
        )

        self.remove_button = icon_button("edit-delete-symbolic")
        get_tooltip_helper().add_tooltip(self.remove_button, _("Remove from history"))
        self.remove_button.add_css_class("flat")
        self.remove_button.connect(
            "clicked", lambda _: self.on_remove_callback(self.transfer.id)
        )

        self.action_box.append(self.cancel_button)
        self.action_box.append(self.remove_button)
        self.add_suffix(self.action_box)

    def update_state(self):
        """Update the display based on transfer status."""
        status = self.transfer.status
        size_str = self._format_file_size(self.transfer.file_size)
        type_str = (
            _("Recebido")
            if self.transfer.transfer_type == TransferType.DOWNLOAD
            else _("Enviado")
        )

        self.set_title(self.transfer.filename)

        self.type_icon.remove_css_class("success")
        self.type_icon.remove_css_class("error")
        self.type_icon.remove_css_class("warning")

        is_final_state = status in [
            TransferStatus.COMPLETED,
            TransferStatus.FAILED,
            TransferStatus.CANCELLED,
        ]

        self.cancel_button.set_visible(not is_final_state)
        self.remove_button.set_visible(is_final_state)

        date_str = ""
        if is_final_state and self.transfer.start_time:
            date_str = datetime.fromtimestamp(self.transfer.start_time).strftime(
                "%Y-%m-%d %H:%M"
            )

        subtitle_parts = [date_str] if date_str else []

        if status == TransferStatus.PENDING:
            subtitle_parts.extend([type_str, size_str, _("Waiting...")])
            self.progress_bar.set_visible(False)
        elif status == TransferStatus.IN_PROGRESS:
            self.progress_bar.set_visible(True)
            self.update_progress()
            return
        elif status == TransferStatus.COMPLETED:
            duration = self.transfer.get_duration()
            duration_str = (
                f"{_('in')} {self._format_duration(duration)}" if duration else ""
            )
            subtitle_parts.extend([
                type_str,
                size_str,
                f"{_('Completed')} {duration_str}",
            ])
            self.progress_bar.set_visible(False)
            self.type_icon.add_css_class("success")
        elif status == TransferStatus.FAILED:
            error_msg = self.transfer.error_message or _("Unknown error")
            subtitle_parts.extend([type_str, size_str, f"{_('Failed')}: {error_msg}"])
            self.progress_bar.set_visible(False)
            self.type_icon.add_css_class("error")
        elif status == TransferStatus.CANCELLED:
            subtitle_parts.extend([type_str, size_str, _("Cancelled")])
            self.progress_bar.set_visible(False)
            self.type_icon.add_css_class("warning")

        self.set_subtitle(" • ".join(filter(None, subtitle_parts)))

    def update_progress(self):
        """Update progress bar and details label for active transfers."""
        if self.transfer.status != TransferStatus.IN_PROGRESS:
            return

        progress = self.transfer.progress
        self.progress_bar.set_fraction(progress / 100.0)

        size_str = self._format_file_size(self.transfer.file_size)
        type_str = (
            _("Recebendo")
            if self.transfer.transfer_type == TransferType.DOWNLOAD
            else _("Enviando")
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
                    details_parts.append(
                        f"{self._format_duration(eta_seconds)} {_('remaining')}"
                    )

        self.set_subtitle(" • ".join(details_parts))

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
        self.handler_ids = []

        self.set_title(_("Transfer Manager"))
        self.set_default_size(600, 500)
        self.set_modal(False)
        self.set_hide_on_close(True)

        self._build_ui()
        self._connect_signals()
        self._populate_transfers()
        self.connect("close-request", self._on_close_request)

        # Apply headerbar transparency
        self._apply_headerbar_transparency()

    def _apply_headerbar_transparency(self):
        """Apply headerbar transparency to the transfer dialog."""
        try:
            if hasattr(self, "parent_window") and self.parent_window:
                settings_manager = getattr(self.parent_window, "settings_manager", None)
                if settings_manager:
                    settings_manager.apply_headerbar_transparency(self.header_bar)
        except Exception as e:
            self.logger.warning(
                f"Failed to apply headerbar transparency to transfer dialog: {e}"
            )

    def _on_close_request(self, window):
        """Safely disconnect all signal handlers before closing."""
        for handler_id in self.handler_ids:
            if self.transfer_manager and GObject.signal_handler_is_connected(
                self.transfer_manager, handler_id
            ):
                self.transfer_manager.disconnect(handler_id)
        self.handler_ids.clear()
        return False

    def _build_ui(self):
        toolbar_view = Adw.ToolbarView()
        self.set_content(toolbar_view)

        self.header_bar = Adw.HeaderBar()
        toolbar_view.add_top_bar(self.header_bar)

        self.clear_history_button = Gtk.Button(label=_("Clear History"))
        self.clear_history_button.add_css_class("destructive-action")
        self.clear_history_button.set_valign(Gtk.Align.CENTER)

        self.clear_history_button.connect("clicked", self._on_clear_all_clicked)

        self.cancel_all_button = Gtk.Button(label=_("Cancelar Tudo"))
        self.cancel_all_button.connect("clicked", self._on_cancel_all_clicked)
        self.header_bar.pack_end(self.cancel_all_button)

        self.bottom_bar = Adw.HeaderBar()
        self.bottom_bar.set_show_title(False)
        self.bottom_bar.set_show_end_title_buttons(False)
        self.bottom_bar.set_show_start_title_buttons(False)
        self.bottom_bar.pack_end(self.clear_history_button)
        toolbar_view.add_bottom_bar(self.bottom_bar)

        # Main content area without PreferencesPage
        self.scrolled = Gtk.ScrolledWindow(vexpand=True, min_content_height=400)
        self.scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)

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
        self.scrolled.set_child(content_box)
        toolbar_view.set_content(self.scrolled)

    def _update_view(self):
        has_transfers = (
            len(self.transfer_manager.active_transfers)
            + len(self.transfer_manager.history)
        ) > 0
        self.transfer_listbox.set_visible(has_transfers)
        self.status_page.set_visible(not has_transfers)
        self._update_clear_history_button_sensitivity()

    def _connect_signals(self):
        """Connect to signals and store handler IDs for safe disconnection."""
        signals = [
            "transfer-started",
            "transfer-completed",
            "transfer-failed",
            "transfer-cancelled",
        ]
        for sig in signals:
            handler_id = self.transfer_manager.connect(sig, self._on_transfer_change)
            self.handler_ids.append(handler_id)

        handler_id = self.transfer_manager.connect(
            "transfer-progress", self._on_transfer_progress
        )
        self.handler_ids.append(handler_id)

    def _populate_transfers(self):
        all_transfers = (
            list(self.transfer_manager.active_transfers.values())
            + self.transfer_manager.history
        )
        all_transfers.sort(key=lambda t: t.start_time or time.time(), reverse=True)
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
            row = TransferRow(
                transfer, self.transfer_manager, self._on_remove_row_clicked
            )
            self.transfer_rows[transfer.id] = row
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
            transfer_obj = manager.get_transfer(transfer_id)
            if transfer_obj:
                row.transfer = transfer_obj
                GLib.idle_add(row.update_progress)

    def _update_cancel_all_button(self):
        self.cancel_all_button.set_visible(
            len(self.transfer_manager.active_transfers) > 0
        )

    def _update_clear_history_button_sensitivity(self):
        self.clear_history_button.set_sensitive(len(self.transfer_manager.history) > 0)

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
                if transfer_id in self.transfer_rows:
                    row = self.transfer_rows.pop(transfer_id)
                    self.transfer_listbox.remove(row)
            self.transfer_manager.history.clear()
            self.transfer_manager._save_history()
            self._update_view()

    def _on_remove_row_clicked(self, transfer_id: str):
        """Callback to remove a single row from the history."""
        # Save current scroll position
        vadjustment = self.scrolled.get_vadjustment()
        current_scroll = vadjustment.get_value() if vadjustment else 0

        if transfer_id in self.transfer_rows:
            row = self.transfer_rows.pop(transfer_id)
            self.transfer_listbox.remove(row)

            self.transfer_manager.history = [
                t for t in self.transfer_manager.history if t.id != transfer_id
            ]
            self.transfer_manager._save_history()
            self._update_view()

            # Restore scroll position
            if vadjustment:
                GLib.idle_add(lambda: vadjustment.set_value(current_scroll))

