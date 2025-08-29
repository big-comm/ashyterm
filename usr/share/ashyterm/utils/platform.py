# ashyterm/utils/platform.py

import os
import shutil
from pathlib import Path
from typing import Dict, List, Optional, Union

from .exceptions import ConfigError
from .logger import get_logger


class PlatformInfo:
    """Information about the current platform (assumed to be Linux)."""

    def __init__(self):
        self.logger = get_logger("ashyterm.platform")
        self.home_dir = Path.home()
        self.config_dir = self._get_config_directory()
        self.cache_dir = self._get_cache_directory()
        self.data_dir = self._get_data_directory()
        self.ssh_dir = self.home_dir / ".ssh"
        self._detect_commands()

    def _get_config_directory(self) -> Path:
        """Get the configuration directory for Linux."""
        if xdg_config := os.environ.get("XDG_CONFIG_HOME"):
            return Path(xdg_config) / "ashyterm"
        return self.home_dir / ".config" / "ashyterm"

    def _get_cache_directory(self) -> Path:
        """Get the cache directory for Linux."""
        if xdg_cache := os.environ.get("XDG_CACHE_HOME"):
            return Path(xdg_cache) / "ashyterm"
        return self.home_dir / ".cache" / "ashyterm"

    def _get_data_directory(self) -> Path:
        """Get the data directory for Linux."""
        if xdg_data := os.environ.get("XDG_DATA_HOME"):
            return Path(xdg_data) / "ashyterm"
        return self.home_dir / ".local" / "share" / "ashyterm"

    def _detect_commands(self):
        """Detect available system commands that are essential for the application."""
        self.commands = {}
        # Only check for commands that the application's logic depends on.
        command_list = ["ssh", "sshpass", "sftp", "rsync"]
        for cmd in command_list:
            if cmd_path := shutil.which(cmd):
                self.commands[cmd] = cmd_path

    def has_command(self, command: str) -> bool:
        """Check if a command is available."""
        return command in self.commands


class PathManager:
    """Path management utilities for Linux."""

    def __init__(self, platform_info: PlatformInfo):
        self.platform_info = platform_info
        self.logger = get_logger("ashyterm.platform.paths")

    def normalize_path(self, path: Union[str, Path]) -> Path:
        """Normalize a path by expanding user and resolving it."""
        path = Path(path).expanduser()
        if not path.is_absolute():
            path = path.resolve()
        return path

    def create_directory_safe(self, directory: Path, mode: int = 0o755) -> bool:
        """Safely create a directory with appropriate permissions."""
        try:
            directory.mkdir(parents=True, exist_ok=True)
            directory.chmod(mode)
            return True
        except Exception as e:
            self.logger.error(f"Failed to create directory {directory}: {e}")
            return False


class CommandBuilder:
    """Build commands for a Linux environment."""

    def __init__(self, platform_info: PlatformInfo):
        self.platform_info = platform_info

    def build_remote_command(
        self,
        command_type: str,
        hostname: str,
        username: Optional[str] = None,
        key_file: Optional[str] = None,
        port: Optional[int] = None,
        options: Optional[Dict[str, str]] = None,
    ) -> List[str]:
        """Builds a remote command (ssh, sftp)."""
        if not self.platform_info.has_command(command_type):
            raise ConfigError(f"{command_type.upper()} command not found")

        cmd = [shutil.which(command_type)]
        if options:
            for key, value in options.items():
                cmd.extend(["-o", f"{key}={value}"])
        if key_file:
            cmd.extend(["-i", key_file])
        if port:
            port_flag = "-P" if command_type == "sftp" else "-p"
            cmd.extend([port_flag, str(port)])
        cmd.append(f"{username}@{hostname}" if username else hostname)
        return cmd


class EnvironmentManager:
    """Manage environment variables for terminal sessions."""

    def __init__(self, platform_info: PlatformInfo):
        self.platform_info = platform_info

    def get_terminal_environment(self) -> Dict[str, str]:
        """Get environment variables for terminal sessions."""
        env = os.environ.copy()
        env["TERM"] = "xterm-256color"
        env["COLORTERM"] = "truecolor"
        if "LANG" not in env:
            import locale

            try:
                system_locale = locale.getdefaultlocale()[0]
                env["LANG"] = f"{system_locale}.UTF-8" if system_locale else "C.UTF-8"
            except Exception:
                env["LANG"] = "C.UTF-8"
        return env


_platform_info: Optional[PlatformInfo] = None


def get_platform_info() -> PlatformInfo:
    """Get the global platform information instance."""
    global _platform_info
    if _platform_info is None:
        _platform_info = PlatformInfo()
    return _platform_info


def get_path_manager() -> PathManager:
    return PathManager(get_platform_info())


def get_command_builder() -> CommandBuilder:
    return CommandBuilder(get_platform_info())


def get_environment_manager() -> EnvironmentManager:
    return EnvironmentManager(get_platform_info())


def get_config_directory() -> Path:
    return get_platform_info().config_dir


def get_ssh_directory() -> Path:
    return get_platform_info().ssh_dir


def has_command(command: str) -> bool:
    return get_platform_info().has_command(command)


def normalize_path(path: Union[str, Path]) -> Path:
    return get_path_manager().normalize_path(path)


def ensure_directory_exists(directory: Union[str, Path], mode: int = 0o755) -> bool:
    """Ensure directory exists, creating it if necessary."""
    try:
        path_manager = get_path_manager()
        directory_path = path_manager.normalize_path(directory)
        if directory_path.exists():
            if not directory_path.is_dir():
                raise ConfigError(
                    f"Path exists but is not a directory: {directory_path}"
                )
            return True
        return path_manager.create_directory_safe(directory_path, mode)
    except ConfigError:
        raise
    except Exception as e:
        logger = get_logger("ashyterm.platform.directory")
        logger.error(f"Failed to ensure directory exists: {directory}: {e}")
        raise ConfigError(f"Failed to create directory: {directory}")
