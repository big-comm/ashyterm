# ashyterm/utils/backup.py

import gzip
import hashlib
import json
import shutil
import threading
import time
from dataclasses import asdict, dataclass
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

from gi.repository import GLib

from .exceptions import StorageCorruptedError, StorageError, StorageWriteError
from .logger import get_logger
from .platform import get_config_directory, get_platform_info


class BackupType(Enum):
    """Types of backups."""

    MANUAL = "manual"
    AUTOMATIC = "automatic"
    SCHEDULED = "scheduled"
    EXPORT = "export"


class BackupStatus(Enum):
    """Backup operation status."""

    SUCCESS = "success"
    FAILED = "failed"
    PARTIAL = "partial"
    CORRUPTED = "corrupted"


@dataclass
class BackupMetadata:
    """Metadata for a backup."""

    timestamp: str
    backup_type: BackupType
    version: str
    file_count: int
    total_size: int
    checksum: str
    description: str
    status: BackupStatus
    error_message: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        data = asdict(self)
        data["backup_type"] = self.backup_type.value
        data["status"] = self.status.value
        return data

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "BackupMetadata":
        """Create from dictionary."""
        data = data.copy()
        data["backup_type"] = BackupType(data["backup_type"])
        data["status"] = BackupStatus(data["status"])
        return cls(**data)


class BackupManager:
    """Manages backup and recovery operations."""

    def __init__(self, backup_dir: Optional[Path] = None):
        self.logger = get_logger("ashyterm.backup")
        self.platform_info = get_platform_info()
        if backup_dir is None:
            backup_dir = get_config_directory() / "backups"
        self.backup_dir = Path(backup_dir)
        self.backup_dir.mkdir(parents=True, exist_ok=True)
        self.max_backups = 10
        self._lock = threading.RLock()
        self.metadata_file = self.backup_dir / "backup_index.json"
        self._load_metadata()
        self.logger.info(
            f"Backup manager initialized with directory: {self.backup_dir}"
        )

    def _load_metadata(self):
        """Load backup metadata index."""
        self.backup_metadata: Dict[str, BackupMetadata] = {}
        if not self.metadata_file.exists():
            return
        try:
            with open(self.metadata_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            for backup_id, meta_dict in data.items():
                try:
                    self.backup_metadata[backup_id] = BackupMetadata.from_dict(
                        meta_dict
                    )
                except Exception as e:
                    self.logger.warning(
                        f"Failed to load metadata for backup {backup_id}: {e}"
                    )
        except Exception as e:
            self.logger.error(f"Failed to load backup metadata: {e}")

    def _save_metadata(self):
        """Save backup metadata index."""
        try:
            data = {
                backup_id: metadata.to_dict()
                for backup_id, metadata in self.backup_metadata.items()
            }
            with open(self.metadata_file, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
        except Exception as e:
            self.logger.error(f"Failed to save backup metadata: {e}")
            raise StorageWriteError(str(self.metadata_file), str(e))

    def create_backup_async(
        self,
        source_files: List[Path],
        backup_type: BackupType = BackupType.MANUAL,
        description: str = "",
    ) -> None:
        """Create a backup asynchronously in a separate thread."""

        def backup_task():
            try:
                self.create_backup(source_files, backup_type, description)
            except Exception as e:
                self.logger.error(f"Async backup task failed: {e}")

        threading.Thread(target=backup_task, daemon=True).start()

    def _generate_backup_id(self, backup_type: BackupType) -> str:
        """Generate unique backup ID."""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        return f"{backup_type.value}_{timestamp}"

    def _calculate_checksum(self, file_path: Path) -> str:
        """Calculate SHA256 checksum of a file."""
        sha256_hash = hashlib.sha256()
        try:
            with open(file_path, "rb") as f:
                for chunk in iter(lambda: f.read(4096), b""):
                    sha256_hash.update(chunk)
            return sha256_hash.hexdigest()
        except Exception as e:
            self.logger.error(f"Failed to calculate checksum for {file_path}: {e}")
            return ""

    def _compress_file(self, source_path: Path, target_path: Path) -> bool:
        """Compress a file using gzip."""
        try:
            with open(source_path, "rb") as f_in, gzip.open(target_path, "wb") as f_out:
                shutil.copyfileobj(f_in, f_out)
            return True
        except Exception as e:
            self.logger.error(f"Failed to compress {source_path}: {e}")
            return False

    def _decompress_file(self, source_path: Path, target_path: Path) -> bool:
        """Decompress a gzip file."""
        try:
            with gzip.open(source_path, "rb") as f_in, open(target_path, "wb") as f_out:
                shutil.copyfileobj(f_in, f_out)
            return True
        except Exception as e:
            self.logger.error(f"Failed to decompress {source_path}: {e}")
            return False

    def create_backup(
        self,
        source_files: List[Path],
        backup_type: BackupType = BackupType.MANUAL,
        description: str = "",
    ) -> Optional[str]:
        """Create a backup of specified files."""
        with self._lock:
            backup_id = self._generate_backup_id(backup_type)
            backup_path = self.backup_dir / backup_id
            try:
                backup_path.mkdir(exist_ok=True)
                copied_files, total_size = [], 0
                for source_file in source_files:
                    if not source_file.exists():
                        self.logger.warning(f"Source file not found: {source_file}")
                        continue
                    target_file = backup_path / f"{source_file.name}.gz"
                    if self._compress_file(source_file, target_file):
                        copied_files.append(target_file)
                        total_size += target_file.stat().st_size
                    else:
                        self.logger.error(f"Failed to backup file: {source_file}")

                if not copied_files:
                    shutil.rmtree(backup_path, ignore_errors=True)
                    raise StorageError(
                        str(backup_path), "No files were successfully backed up"
                    )

                backup_checksum = self._calculate_backup_checksum(backup_path)
                metadata = BackupMetadata(
                    timestamp=datetime.now().isoformat(),
                    backup_type=backup_type,
                    version="1.1.0",
                    file_count=len(copied_files),
                    total_size=total_size,
                    checksum=backup_checksum,
                    description=description,
                    status=BackupStatus.SUCCESS,
                )
                with open(
                    backup_path / "backup_metadata.json", "w", encoding="utf-8"
                ) as f:
                    json.dump(metadata.to_dict(), f, indent=2, ensure_ascii=False)

                self.backup_metadata[backup_id] = metadata
                self._save_metadata()
                self.logger.info(f"Backup created successfully: {backup_id}")
                GLib.idle_add(self._cleanup_old_backups)
                return backup_id
            except Exception as e:
                if backup_path.exists():
                    shutil.rmtree(backup_path, ignore_errors=True)
                self.logger.error(f"Failed to create backup: {e}")
                error_metadata = BackupMetadata(
                    timestamp=datetime.now().isoformat(),
                    backup_type=backup_type,
                    version="1.1.0",
                    file_count=0,
                    total_size=0,
                    checksum="",
                    description=description,
                    status=BackupStatus.FAILED,
                    error_message=str(e),
                )
                self.backup_metadata[backup_id] = error_metadata
                self._save_metadata()
                raise StorageError(str(backup_path), f"backup creation failed: {e}")

    def _calculate_backup_checksum(self, backup_path: Path) -> str:
        """Calculate checksum for entire backup directory."""
        sha256_hash = hashlib.sha256()
        files = sorted(
            f
            for f in backup_path.glob("*")
            if f.is_file() and f.name != "backup_metadata.json"
        )
        for file_path in files:
            sha256_hash.update(self._calculate_checksum(file_path).encode("utf-8"))
        return sha256_hash.hexdigest()

    def restore_backup(
        self, backup_id: str, target_dir: Path, verify_integrity: bool = True
    ) -> bool:
        """Restore a backup to target directory."""
        with self._lock:
            if backup_id not in self.backup_metadata:
                raise StorageError(backup_id, "Backup not found")
            backup_path = self.backup_dir / backup_id
            if not backup_path.exists():
                raise StorageError(str(backup_path), "Backup directory not found")
            try:
                if verify_integrity and not self.verify_backup(backup_id):
                    raise StorageCorruptedError(
                        str(backup_path), "Backup integrity check failed"
                    )

                target_dir.mkdir(parents=True, exist_ok=True)
                restored_count = 0
                for backup_file in backup_path.glob("*.gz"):
                    target_name = backup_file.name[:-3]
                    target_file = target_dir / target_name
                    if self._decompress_file(backup_file, target_file):
                        restored_count += 1
                    else:
                        self.logger.error(f"Failed to restore file: {backup_file}")

                if restored_count == 0:
                    raise StorageError(
                        str(backup_path), "No files were successfully restored"
                    )

                self.logger.info(
                    f"Backup restored successfully: {backup_id} ({restored_count} files)"
                )
                return True
            except Exception as e:
                self.logger.error(f"Failed to restore backup {backup_id}: {e}")
                raise StorageError(str(backup_path), f"restore failed: {e}")

    def verify_backup(self, backup_id: str) -> bool:
        """Verify backup integrity."""
        if backup_id not in self.backup_metadata:
            return False
        metadata = self.backup_metadata[backup_id]
        backup_path = self.backup_dir / backup_id
        if (
            not backup_path.exists()
            or not (backup_path / "backup_metadata.json").exists()
        ):
            return False
        try:
            backup_files = [
                f for f in backup_path.glob("*") if f.name != "backup_metadata.json"
            ]
            if len(backup_files) != metadata.file_count:
                self.logger.warning(f"File count mismatch in backup {backup_id}")
                return False
            if self._calculate_backup_checksum(backup_path) != metadata.checksum:
                self.logger.warning(f"Checksum mismatch in backup {backup_id}")
                return False
            return True
        except Exception as e:
            self.logger.error(f"Error verifying backup {backup_id}: {e}")
            return False

    def list_backups(
        self, backup_type: Optional[BackupType] = None
    ) -> List[Tuple[str, BackupMetadata]]:
        """List available backups."""
        backups = [
            (bid, meta)
            for bid, meta in self.backup_metadata.items()
            if backup_type is None or meta.backup_type == backup_type
        ]
        backups.sort(key=lambda x: x[1].timestamp, reverse=True)
        return backups

    def delete_backup(self, backup_id: str) -> bool:
        """Delete a backup."""
        with self._lock:
            if backup_id not in self.backup_metadata:
                return False
            backup_path = self.backup_dir / backup_id
            try:
                if backup_path.exists():
                    shutil.rmtree(backup_path)
                del self.backup_metadata[backup_id]
                self._save_metadata()
                self.logger.info(f"Backup deleted: {backup_id}")
                return True
            except Exception as e:
                self.logger.error(f"Failed to delete backup {backup_id}: {e}")
                return False

    def _cleanup_old_backups(self):
        """Clean up old backups based on retention policy."""
        with self._lock:
            try:
                backups_by_type = {}
                for backup_id, metadata in self.backup_metadata.items():
                    backups_by_type.setdefault(metadata.backup_type, []).append((
                        backup_id,
                        metadata,
                    ))

                for backup_type, backups in backups_by_type.items():
                    backups.sort(key=lambda x: x[1].timestamp, reverse=True)
                    max_count = (
                        5 if backup_type == BackupType.AUTOMATIC else self.max_backups
                    )
                    if len(backups) > max_count:
                        for backup_id, _ in backups[max_count:]:
                            self.logger.info(f"Cleaning up old backup: {backup_id}")
                            self.delete_backup(backup_id)
            except Exception as e:
                self.logger.error(f"Error during backup cleanup: {e}")
        return False


class AutoBackupScheduler:
    """Automatic backup scheduler."""

    def __init__(self, backup_manager: BackupManager):
        self.backup_manager = backup_manager
        self.logger = get_logger("ashyterm.backup.scheduler")
        self.enabled = True
        self.last_backup_time = 0

    def should_backup(self) -> bool:
        """Check if automatic backup should be performed."""
        if not self.enabled:
            return False
        time_since_last = time.time() - self.last_backup_time
        return time_since_last >= self.backup_manager.auto_backup_interval

    def perform_auto_backup(self, source_files: List[Path]) -> Optional[str]:
        """Perform automatic backup if needed."""
        if not self.should_backup():
            return None
        try:
            backup_id = self.backup_manager.create_backup(
                source_files, BackupType.AUTOMATIC, "Automatic backup"
            )
            if backup_id:
                self.last_backup_time = time.time()
                self.logger.info(f"Automatic backup completed: {backup_id}")
            return backup_id
        except Exception as e:
            self.logger.error(f"Automatic backup failed: {e}")
            return None

    def enable(self):
        self.enabled = True
        self.logger.info("Automatic backups enabled")

    def disable(self):
        self.enabled = False
        self.logger.info("Automatic backups disabled")


_backup_manager: Optional[BackupManager] = None


def get_backup_manager() -> BackupManager:
    """Get the global backup manager instance."""
    global _backup_manager
    if _backup_manager is None:
        _backup_manager = BackupManager()
    return _backup_manager


def create_backup(
    source_files: List[Union[str, Path]], description: str = ""
) -> Optional[str]:
    """Create a backup of specified files."""
    path_list = [Path(f) for f in source_files]
    return get_backup_manager().create_backup(path_list, BackupType.MANUAL, description)


def restore_backup(backup_id: str, target_dir: Union[str, Path]) -> bool:
    """Restore a backup."""
    return get_backup_manager().restore_backup(backup_id, Path(target_dir))


def list_available_backups() -> List[Tuple[str, Dict[str, Any]]]:
    """List all available backups."""
    backups = get_backup_manager().list_backups()
    return [(backup_id, metadata.to_dict()) for backup_id, metadata in backups]


def verify_backup_integrity(backup_id: str) -> bool:
    """Verify backup integrity."""
    return get_backup_manager().verify_backup(backup_id)


def cleanup_old_backups():
    """Clean up old backups according to retention policy."""
    get_backup_manager()._cleanup_old_backups()
