import json
import os
import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, GObject, Gtk

from ..utils.logger import get_logger
from ..utils.translation_utils import _


class TransferType(Enum):
    DOWNLOAD = "download"
    UPLOAD = "upload"


class TransferStatus(Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass
class TransferItem:
    id: str
    filename: str
    local_path: str
    remote_path: str
    file_size: int
    transfer_type: TransferType
    status: TransferStatus
    start_time: Optional[float] = None
    end_time: Optional[float] = None
    progress: float = 0.0
    error_message: Optional[str] = None
    is_cancellable: bool = False
    cancellation_event: threading.Event = field(
        default_factory=threading.Event, repr=False
    )

    def get_duration(self) -> Optional[float]:
        if self.start_time and self.end_time:
            return self.end_time - self.start_time
        return None

class TransferManager(GObject.Object):
    __gsignals__ = {
        "transfer-started": (GObject.SignalFlags.RUN_FIRST, None, (str,)),
        "transfer-progress": (GObject.SignalFlags.RUN_FIRST, None, (str, float)),
        "transfer-completed": (GObject.SignalFlags.RUN_FIRST, None, (str,)),
        "transfer-failed": (GObject.SignalFlags.RUN_FIRST, None, (str, str)),
        "transfer-cancelled": (GObject.SignalFlags.RUN_FIRST, None, (str,)),
    }

    def __init__(self, config_dir: str, file_operations=None):
        super().__init__()
        self.logger = get_logger(__name__)
        self.config_dir = config_dir
        self.history_file = os.path.join(config_dir, "transfer_history.json")
        self.file_operations = file_operations
        self.active_transfers: Dict[str, TransferItem] = {}
        self.history: List[TransferItem] = []

        self.progress_revealer: Optional[Gtk.Revealer] = None
        self.progress_row: Optional[Adw.ActionRow] = None  # Reference to the ActionRow
        self.progress_bar: Optional[Gtk.ProgressBar] = None
        self.cancel_button: Optional[Gtk.Button] = None

        # self.progress_revealer use red background

        self._load_history()

    def _load_history(self):
        try:
            if os.path.exists(self.history_file):
                with open(self.history_file, "r") as f:
                    data = json.load(f)
                    for item_data in data:
                        # Re-hydrate enums
                        item_data["transfer_type"] = TransferType(
                            item_data["transfer_type"]
                        )
                        item_data["status"] = TransferStatus(item_data["status"])
                        # These are not saved, so they are not in item_data
                        item_data.pop("cancellation_event", None)
                        item_data.pop("is_cancellable", None)
                        self.history.append(TransferItem(**item_data))
            # Keep history trimmed
            self.history = self.history[:50]
        except Exception as e:
            self.logger.error(f"Failed to load transfer history: {e}")

    def _save_history(self):
        try:
            os.makedirs(os.path.dirname(self.history_file), exist_ok=True)
            data_to_save = []
            # Persist only the last 50 items
            for item in self.history[:50]:
                # Create a serializable dictionary, excluding non-JSON types
                serializable_item = {
                    "id": item.id,
                    "filename": item.filename,
                    "local_path": item.local_path,
                    "remote_path": item.remote_path,
                    "file_size": item.file_size,
                    "transfer_type": item.transfer_type.value,
                    "status": item.status.value,
                    "start_time": item.start_time,
                    "end_time": item.end_time,
                    "progress": item.progress,
                    "error_message": item.error_message,
                }
                data_to_save.append(serializable_item)

            with open(self.history_file, "w") as f:
                json.dump(data_to_save, f, indent=2)
        except Exception as e:
            self.logger.error(f"Failed to save transfer history: {e}")

    def add_transfer(
        self,
        filename: str,
        local_path: str,
        remote_path: str,
        file_size: int,
        transfer_type: TransferType,
        is_cancellable: bool = False,
    ) -> str:
        transfer_id = f"{int(time.time() * 1000)}_{len(self.active_transfers)}"
        transfer_item = TransferItem(
            id=transfer_id,
            filename=filename,
            local_path=local_path,
            remote_path=remote_path,
            file_size=file_size,
            transfer_type=transfer_type,
            status=TransferStatus.PENDING,
            is_cancellable=is_cancellable,
        )
        self.active_transfers[transfer_id] = transfer_item
        return transfer_id

    def start_transfer(self, transfer_id: str):
        if transfer_id in self.active_transfers:
            transfer = self.active_transfers[transfer_id]
            transfer.status = TransferStatus.IN_PROGRESS
            transfer.start_time = time.time()
            self.emit("transfer-started", transfer_id)
            self._update_progress_display()

    def update_progress(self, transfer_id: str, progress: float):
        if transfer_id in self.active_transfers:
            self.active_transfers[transfer_id].progress = progress
            self.emit("transfer-progress", transfer_id, progress)
            self._update_progress_display()

    def complete_transfer(self, transfer_id: str):
        if transfer_id in self.active_transfers:
            transfer = self.active_transfers.pop(transfer_id)
            transfer.status = TransferStatus.COMPLETED
            transfer.end_time = time.time()
            transfer.progress = 100.0
            self.history.insert(0, transfer)
            self.emit("transfer-completed", transfer_id)
            self._save_history()
            self._update_progress_display()

    def fail_transfer(self, transfer_id: str, error_message: str):
        if transfer_id in self.active_transfers:
            transfer = self.active_transfers.pop(transfer_id)
            if "cancel" in error_message.lower():
                transfer.status = TransferStatus.CANCELLED
                self.emit("transfer-cancelled", transfer_id)
            else:
                transfer.status = TransferStatus.FAILED
                self.emit("transfer-failed", transfer_id, error_message)

            transfer.end_time = time.time()
            transfer.error_message = error_message
            self.history.insert(0, transfer)
            self._save_history()
            self._update_progress_display()

    def cancel_transfer(self, transfer_id: str):
        if transfer_id in self.active_transfers:
            transfer = self.active_transfers[transfer_id]
            if transfer.is_cancellable:
                transfer.cancellation_event.set()
                self.logger.info(f"Cancellation requested for transfer {transfer_id}")

    def get_cancellation_event(self, transfer_id: str) -> Optional[threading.Event]:
        if transfer_id in self.active_transfers:
            return self.active_transfers[transfer_id].cancellation_event
        return None

    def get_transfer(self, transfer_id: str) -> Optional[TransferItem]:
        return self.active_transfers.get(transfer_id)

    def _update_progress_display(self):
        if self.progress_revealer:
            GLib.idle_add(self._do_update_progress_display)

    def _do_update_progress_display(self):
        has_active = len(self.active_transfers) > 0
        self.progress_revealer.set_reveal_child(has_active)

        if not has_active:
            return False

        if len(self.active_transfers) == 1:
            transfer = next(iter(self.active_transfers.values()))
            self.progress_row.set_title(f"Transferring {transfer.filename}")

            elapsed = time.time() - (transfer.start_time or time.time())
            subtitle_parts = [f"{transfer.progress:.1f}%"]

            if elapsed > 0.5 and transfer.file_size > 0:
                bytes_transferred = (transfer.progress / 100.0) * transfer.file_size
                speed = bytes_transferred / elapsed
                subtitle_parts.append(f"{self._format_speed(speed)}")

                if speed > 0:
                    remaining_bytes = transfer.file_size - bytes_transferred
                    eta = remaining_bytes / speed
                    subtitle_parts.append(f"{self._format_duration(eta)} left")

            self.progress_row.set_subtitle(" • ".join(subtitle_parts))
            self.progress_bar.set_fraction(transfer.progress / 100.0)
        else:
            total_progress = sum(t.progress for t in self.active_transfers.values())
            overall_progress = total_progress / len(self.active_transfers)
            self.progress_row.set_title(
                f"Transferring {len(self.active_transfers)} files"
            )
            self.progress_row.set_subtitle(f"Overall progress: {overall_progress:.1f}%")
            self.progress_bar.set_fraction(overall_progress / 100.0)

        return False

    def _on_cancel_all_clicked(self, button):
        for transfer_id in list(self.active_transfers.keys()):
            self.cancel_transfer(transfer_id)

    def create_progress_widget(self) -> Gtk.Widget:
        self.progress_revealer = Gtk.Revealer(
            transition_type=Gtk.RevealerTransitionType.SLIDE_DOWN,
        )
        self.progress_revealer.add_css_class("background")

        # Create the ActionRow and store a reference to it
        self.progress_row = Adw.ActionRow()

        self.progress_bar = Gtk.ProgressBar(valign=Gtk.Align.CENTER, hexpand=True)
        self.progress_row.add_prefix(self.progress_bar)

        self.cancel_button = Gtk.Button.new_from_icon_name("process-stop-symbolic")
        self.cancel_button.set_tooltip_text(_("Cancel All Transfers"))
        self.cancel_button.set_valign(Gtk.Align.CENTER)
        self.cancel_button.add_css_class("flat")
        self.cancel_button.add_css_class("destructive-action")
        self.cancel_button.connect("clicked", self._on_cancel_all_clicked)
        self.progress_row.add_suffix(self.cancel_button)

        self.progress_revealer.set_child(self.progress_row)
        return self.progress_revealer

    def _format_file_size(self, size_bytes: int) -> str:
        if not isinstance(size_bytes, (int, float)) or size_bytes < 0:
            return "0 B"
        if size_bytes < 1024:
            return f"{size_bytes} B"
        if size_bytes < 1024**2:
            return f"{size_bytes / 1024:.1f} KB"
        if size_bytes < 1024**3:
            return f"{size_bytes / 1024**2:.1f} MB"
        return f"{size_bytes / 1024**3:.1f} GB"

    def _format_speed(self, bytes_per_second: float) -> str:
        if not isinstance(bytes_per_second, (int, float)) or bytes_per_second <= 0:
            return "0 B/s"
        if bytes_per_second < 1024:
            return f"{bytes_per_second:.1f} B/s"
        if bytes_per_second < 1024**2:
            return f"{bytes_per_second / 1024:.1f} KB/s"
        if bytes_per_second < 1024**3:
            return f"{bytes_per_second / 1024**2:.1f} MB/s"
        return f"{bytes_per_second / 1024**3:.1f} GB/s"

    def _format_duration(self, seconds: float) -> str:
        if not isinstance(seconds, (int, float)) or seconds < 0:
            return "0s"
        seconds = int(seconds)
        if seconds < 60:
            return f"{seconds}s"
        minutes, seconds = divmod(seconds, 60)
        if minutes < 60:
            return f"{minutes}m {seconds}s"
        hours, minutes = divmod(minutes, 60)
        return f"{hours}h {minutes}m"
