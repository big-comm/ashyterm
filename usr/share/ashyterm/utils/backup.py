# ashyterm/utils/backup.py
"""
Backup and recovery utilities for Ashy Terminal.

This module provides robust backup and recovery functionality for sessions,
settings, and other application data with versioning, integrity checks,
and automatic cleanup.
"""

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
    platform: str
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
        """
        Initialize backup manager.

        Args:
            backup_dir: Directory for storing backups
        """
        self.logger = get_logger("ashyterm.backup")
        self.platform_info = get_platform_info()

        # Set up backup directory
        if backup_dir is None:
            backup_dir = get_config_directory() / "backups"

        self.backup_dir = Path(backup_dir)
        self.backup_dir.mkdir(parents=True, exist_ok=True)

        # Configuration
        self.max_backups = 10
        self.auto_backup_interval = 24 * 60 * 60  # 24 hours in seconds
        self.compression_enabled = True
        self.verify_backups = True

        # Thread safety
        self._lock = threading.RLock()

        # Metadata file
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
                    metadata = BackupMetadata.from_dict(meta_dict)
                    self.backup_metadata[backup_id] = metadata
                except Exception as e:
                    self.logger.warning(
                        f"Failed to load metadata for backup {backup_id}: {e}"
                    )

        except Exception as e:
            self.logger.error(f"Failed to load backup metadata: {e}")

    def _save_metadata(self):
        """Save backup metadata index."""
        try:
            data = {}
            for backup_id, metadata in self.backup_metadata.items():
                data[backup_id] = metadata.to_dict()

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
        """
        Create a backup asynchronously in a separate thread.
        """
        self.logger.debug(f"Scheduling async backup: {description}")

        def backup_task():
            try:
                self.create_backup(source_files, backup_type, description)
            except Exception as e:
                self.logger.error(f"Async backup task failed: {e}")

        # Run the backup task in a daemon thread
        thread = threading.Thread(target=backup_task, daemon=True)
        thread.start()

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
            with open(source_path, "rb") as f_in:
                with gzip.open(target_path, "wb") as f_out:
                    shutil.copyfileobj(f_in, f_out)
            return True
        except Exception as e:
            self.logger.error(f"Failed to compress {source_path}: {e}")
            return False

    def _decompress_file(self, source_path: Path, target_path: Path) -> bool:
        """Decompress a gzip file."""
        try:
            with gzip.open(source_path, "rb") as f_in:
                with open(target_path, "wb") as f_out:
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
        """
        Create a backup of specified files.

        Args:
            source_files: List of files to backup
            backup_type: Type of backup
            description: Description of the backup

        Returns:
            Backup ID if successful, None otherwise
        """
        with self._lock:
            backup_id = self._generate_backup_id(backup_type)
            backup_path = self.backup_dir / backup_id

            try:
                # Create backup directory
                backup_path.mkdir(exist_ok=True)

                # Copy files
                copied_files = []
                total_size = 0

                for source_file in source_files:
                    if not source_file.exists():
                        self.logger.warning(f"Source file not found: {source_file}")
                        continue

                    # Determine target filename
                    relative_path = source_file.name
                    if self.compression_enabled:
                        target_file = backup_path / f"{relative_path}.gz"
                        success = self._compress_file(source_file, target_file)
                    else:
                        target_file = backup_path / relative_path
                        try:
                            shutil.copy2(source_file, target_file)
                            success = True
                        except Exception as e:
                            self.logger.error(f"Failed to copy {source_file}: {e}")
                            success = False

                    if success:
                        copied_files.append(target_file)
                        total_size += target_file.stat().st_size
                    else:
                        self.logger.error(f"Failed to backup file: {source_file}")

                if not copied_files:
                    # Clean up empty backup directory
                    shutil.rmtree(backup_path, ignore_errors=True)
                    raise StorageError(
                        str(backup_path), "No files were successfully backed up"
                    )

                # Calculate backup checksum
                backup_checksum = self._calculate_backup_checksum(backup_path)

                # Create metadata
                metadata = BackupMetadata(
                    timestamp=datetime.now().isoformat(),
                    backup_type=backup_type,
                    version="1.0.1",  # Application version
                    platform=self.platform_info.platform_type.value,
                    file_count=len(copied_files),
                    total_size=total_size,
                    checksum=backup_checksum,
                    description=description,
                    status=BackupStatus.SUCCESS,
                )

                # Save metadata to backup directory
                metadata_file = backup_path / "backup_metadata.json"
                with open(metadata_file, "w", encoding="utf-8") as f:
                    json.dump(metadata.to_dict(), f, indent=2, ensure_ascii=False)

                # Add to global metadata
                self.backup_metadata[backup_id] = metadata
                self._save_metadata()

                self.logger.info(f"Backup created successfully: {backup_id}")

                # Defer cleanup of old backups
                GLib.idle_add(self._cleanup_old_backups)

                return backup_id

            except Exception as e:
                # Clean up failed backup
                if backup_path.exists():
                    shutil.rmtree(backup_path, ignore_errors=True)

                self.logger.error(f"Failed to create backup: {e}")

                # Record failed backup
                error_metadata = BackupMetadata(
                    timestamp=datetime.now().isoformat(),
                    backup_type=backup_type,
                    version="1.0.1",
                    platform=self.platform_info.platform_type.value,
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

        # Sort files for consistent checksum
        files = sorted(backup_path.glob("*"))

        for file_path in files:
            if file_path.is_file() and file_path.name != "backup_metadata.json":
                file_checksum = self._calculate_checksum(file_path)
                sha256_hash.update(file_checksum.encode("utf-8"))

        return sha256_hash.hexdigest()

    def restore_backup(
        self, backup_id: str, target_dir: Path, verify_integrity: bool = True
    ) -> bool:
        """
        Restore a backup to target directory.

        Args:
            backup_id: ID of backup to restore
            target_dir: Directory to restore files to
            verify_integrity: Whether to verify backup integrity

        Returns:
            True if restore successful
        """
        with self._lock:
            if backup_id not in self.backup_metadata:
                raise StorageError(backup_id, "Backup not found")

            backup_path = self.backup_dir / backup_id

            if not backup_path.exists():
                raise StorageError(str(backup_path), "Backup directory not found")

            try:
                # Verify integrity if requested
                if verify_integrity:
                    if not self.verify_backup(backup_id):
                        raise StorageCorruptedError(
                            str(backup_path), "Backup integrity check failed"
                        )

                # Create target directory
                target_dir.mkdir(parents=True, exist_ok=True)

                # Restore files
                restored_count = 0
                backup_files = list(backup_path.glob("*"))

                for backup_file in backup_files:
                    if backup_file.name == "backup_metadata.json":
                        continue

                    # Determine target filename
                    if backup_file.name.endswith(".gz"):
                        target_name = backup_file.name[:-3]  # Remove .gz extension
                        target_file = target_dir / target_name
                        success = self._decompress_file(backup_file, target_file)
                    else:
                        target_file = target_dir / backup_file.name
                        try:
                            shutil.copy2(backup_file, target_file)
                            success = True
                        except Exception as e:
                            self.logger.error(f"Failed to restore {backup_file}: {e}")
                            success = False

                    if success:
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
        """
        Verify backup integrity.

        Args:
            backup_id: ID of backup to verify

        Returns:
            True if backup is valid
        """
        if backup_id not in self.backup_metadata:
            return False

        metadata = self.backup_metadata[backup_id]
        backup_path = self.backup_dir / backup_id

        if not backup_path.exists():
            return False

        try:
            # Check if metadata file exists
            metadata_file = backup_path / "backup_metadata.json"
            if not metadata_file.exists():
                return False

            # Verify file count
            backup_files = [
                f for f in backup_path.glob("*") if f.name != "backup_metadata.json"
            ]
            if len(backup_files) != metadata.file_count:
                self.logger.warning(f"File count mismatch in backup {backup_id}")
                return False

            # Verify checksum
            current_checksum = self._calculate_backup_checksum(backup_path)
            if current_checksum != metadata.checksum:
                self.logger.warning(f"Checksum mismatch in backup {backup_id}")
                return False

            return True

        except Exception as e:
            self.logger.error(f"Error verifying backup {backup_id}: {e}")
            return False

    def list_backups(
        self, backup_type: Optional[BackupType] = None
    ) -> List[Tuple[str, BackupMetadata]]:
        """
        List available backups.

        Args:
            backup_type: Filter by backup type

        Returns:
            List of (backup_id, metadata) tuples
        """
        backups = []

        for backup_id, metadata in self.backup_metadata.items():
            if backup_type is None or metadata.backup_type == backup_type:
                backups.append((backup_id, metadata))

        # Sort by timestamp (newest first)
        backups.sort(key=lambda x: x[1].timestamp, reverse=True)

        return backups

    def delete_backup(self, backup_id: str) -> bool:
        """
        Delete a backup.

        Args:
            backup_id: ID of backup to delete

        Returns:
            True if deletion successful
        """
        with self._lock:
            if backup_id not in self.backup_metadata:
                return False

            backup_path = self.backup_dir / backup_id

            try:
                # Remove backup directory
                if backup_path.exists():
                    shutil.rmtree(backup_path)

                # Remove from metadata
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
                # Group backups by type
                backups_by_type = {}
                for backup_id, metadata in self.backup_metadata.items():
                    backup_type = metadata.backup_type
                    if backup_type not in backups_by_type:
                        backups_by_type[backup_type] = []
                    backups_by_type[backup_type].append((backup_id, metadata))

                # Clean up each type separately
                for backup_type, backups in backups_by_type.items():
                    # Sort by timestamp (newest first)
                    backups.sort(key=lambda x: x[1].timestamp, reverse=True)

                    # Keep only the most recent backups
                    max_count = self.max_backups
                    if backup_type == BackupType.AUTOMATIC:
                        max_count = min(
                            5, self.max_backups
                        )  # Keep fewer automatic backups

                    if len(backups) > max_count:
                        backups_to_delete = backups[max_count:]

                        for backup_id, _ in backups_to_delete:
                            self.logger.info(f"Cleaning up old backup: {backup_id}")
                            self.delete_backup(backup_id)

            except Exception as e:
                self.logger.error(f"Error during backup cleanup: {e}")

        return False  # Prevent idle_add from repeating

    def export_backup(self, backup_id: str, export_path: Path) -> bool:
        """
        Export a backup to external location.

        Args:
            backup_id: ID of backup to export
            export_path: Path to export to

        Returns:
            True if export successful
        """
        if backup_id not in self.backup_metadata:
            raise StorageError(backup_id, "Backup not found")

        backup_path = self.backup_dir / backup_id

        if not backup_path.exists():
            raise StorageError(str(backup_path), "Backup directory not found")

        try:
            # Create export archive
            shutil.make_archive(str(export_path), "zip", str(backup_path))

            self.logger.info(f"Backup exported: {backup_id} -> {export_path}.zip")
            return True

        except Exception as e:
            self.logger.error(f"Failed to export backup {backup_id}: {e}")
            raise StorageError(str(export_path), f"export failed: {e}")

    def import_backup(self, import_path: Path, description: str = "") -> Optional[str]:
        """
        Import a backup from external archive.

        Args:
            import_path: Path to backup archive
            description: Description for imported backup

        Returns:
            Backup ID if successful
        """
        if not import_path.exists():
            raise StorageError(str(import_path), "Import file not found")

        backup_id = self._generate_backup_id(BackupType.MANUAL)
        backup_path = self.backup_dir / backup_id

        try:
            # Extract archive
            shutil.unpack_archive(str(import_path), str(backup_path))

            # Load metadata
            metadata_file = backup_path / "backup_metadata.json"
            if metadata_file.exists():
                with open(metadata_file, "r", encoding="utf-8") as f:
                    meta_dict = json.load(f)

                metadata = BackupMetadata.from_dict(meta_dict)
                metadata.backup_type = BackupType.MANUAL  # Mark as manual import
                metadata.description = (
                    f"Imported: {description}"
                    if description
                    else f"Imported from {import_path.name}"
                )
            else:
                # Create metadata for backups without it
                backup_files = [f for f in backup_path.glob("*") if f.is_file()]
                total_size = sum(f.stat().st_size for f in backup_files)

                metadata = BackupMetadata(
                    timestamp=datetime.now().isoformat(),
                    backup_type=BackupType.MANUAL,
                    version="unknown",
                    platform="unknown",
                    file_count=len(backup_files),
                    total_size=total_size,
                    checksum=self._calculate_backup_checksum(backup_path),
                    description=f"Imported from {import_path.name}",
                    status=BackupStatus.SUCCESS,
                )

            # Add to metadata
            self.backup_metadata[backup_id] = metadata
            self._save_metadata()

            self.logger.info(f"Backup imported: {backup_id}")
            return backup_id

        except Exception as e:
            # Clean up failed import
            if backup_path.exists():
                shutil.rmtree(backup_path, ignore_errors=True)

            self.logger.error(f"Failed to import backup: {e}")
            raise StorageError(str(import_path), f"import failed: {e}")

    def get_backup_size(self, backup_id: str) -> int:
        """Get total size of a backup in bytes."""
        if backup_id in self.backup_metadata:
            return self.backup_metadata[backup_id].total_size
        return 0

    def get_total_backup_size(self) -> int:
        """Get total size of all backups in bytes."""
        return sum(meta.total_size for meta in self.backup_metadata.values())


class AutoBackupScheduler:
    """Automatic backup scheduler."""

    def __init__(self, backup_manager: BackupManager):
        """
        Initialize auto backup scheduler.

        Args:
            backup_manager: BackupManager instance
        """
        self.backup_manager = backup_manager
        self.logger = get_logger("ashyterm.backup.scheduler")
        self.enabled = True
        self.last_backup_time = 0
        self._timer = None

    def should_backup(self) -> bool:
        """Check if automatic backup should be performed."""
        if not self.enabled:
            return False

        current_time = time.time()
        time_since_last = current_time - self.last_backup_time

        return time_since_last >= self.backup_manager.auto_backup_interval

    def perform_auto_backup(self, source_files: List[Path]) -> Optional[str]:
        """
        Perform automatic backup if needed.

        Args:
            source_files: Files to backup

        Returns:
            Backup ID if backup was performed
        """
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
        """Enable automatic backups."""
        self.enabled = True
        self.logger.info("Automatic backups enabled")

    def disable(self):
        """Disable automatic backups."""
        self.enabled = False
        self.logger.info("Automatic backups disabled")


# Global backup manager instance
_backup_manager: Optional[BackupManager] = None


def get_backup_manager() -> BackupManager:
    """
    Get the global backup manager instance.

    Returns:
        BackupManager instance
    """
    global _backup_manager
    if _backup_manager is None:
        _backup_manager = BackupManager()
    return _backup_manager


def create_backup(
    source_files: List[Union[str, Path]], description: str = ""
) -> Optional[str]:
    """
    Create a backup of specified files.

    Args:
        source_files: List of files to backup
        description: Backup description

    Returns:
        Backup ID if successful
    """
    path_list = [Path(f) for f in source_files]
    return get_backup_manager().create_backup(path_list, BackupType.MANUAL, description)


def restore_backup(backup_id: str, target_dir: Union[str, Path]) -> bool:
    """
    Restore a backup.

    Args:
        backup_id: ID of backup to restore
        target_dir: Directory to restore to

    Returns:
        True if successful
    """
    return get_backup_manager().restore_backup(backup_id, Path(target_dir))


def list_available_backups() -> List[Tuple[str, Dict[str, Any]]]:
    """
    List all available backups.

    Returns:
        List of (backup_id, metadata_dict) tuples
    """
    backups = get_backup_manager().list_backups()
    return [(backup_id, metadata.to_dict()) for backup_id, metadata in backups]


def verify_backup_integrity(backup_id: str) -> bool:
    """
    Verify backup integrity.

    Args:
        backup_id: ID of backup to verify

    Returns:
        True if backup is valid
    """
    return get_backup_manager().verify_backup(backup_id)


def cleanup_old_backups():
    """Clean up old backups according to retention policy."""
    get_backup_manager()._cleanup_old_backups()
