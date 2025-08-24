import os
import sys
import platform
import shutil
import subprocess
from pathlib import Path
from typing import Optional, List, Dict, Any, Tuple, Union
from enum import Enum

from .logger import get_logger
from .exceptions import (
    AshyTerminalError, ErrorCategory, ErrorSeverity,
    ConfigError, ValidationError
)


class PlatformType(Enum):
    """Supported platform types."""
    LINUX = "linux"
    WINDOWS = "windows"
    MACOS = "macos"
    BSD = "bsd"
    UNKNOWN = "unknown"


class ShellType(Enum):
    """Supported shell types."""
    BASH = "bash"
    ZSH = "zsh"
    FISH = "fish"
    SH = "sh"
    DASH = "dash"
    TCSH = "tcsh"
    CSH = "csh"
    KSH = "ksh"
    POWERSHELL = "powershell"
    CMD = "cmd"
    UNKNOWN = "unknown"


class PlatformInfo:
    """Information about the current platform."""
    
    def __init__(self):
        self.logger = get_logger('ashyterm.platform')
        self._detect_platform()
        self._detect_architecture()
        self._detect_shell()
        self._detect_paths()
        self._detect_commands()
    
    def _detect_platform(self):
        """Detect the current platform."""
        system = platform.system().lower()
        
        if system == 'linux':
            self.platform_type = PlatformType.LINUX
        elif system == 'windows':
            self.platform_type = PlatformType.WINDOWS
        elif system == 'darwin':
            self.platform_type = PlatformType.MACOS
        elif 'bsd' in system:
            self.platform_type = PlatformType.BSD
        else:
            self.platform_type = PlatformType.UNKNOWN
        
        self.system_name = system
        self.platform_release = platform.release()
        self.platform_version = platform.version()
        
        self.logger.debug(f"Detected platform: {self.platform_type.value} ({system})")
    
    def _detect_architecture(self):
        """Detect system architecture."""
        self.architecture = platform.machine()
        self.processor = platform.processor()
        self.is_64bit = sys.maxsize > 2**32
        
        self.logger.debug(f"Architecture: {self.architecture}, 64-bit: {self.is_64bit}")
    
    def _detect_shell(self):
        """Detect available shells."""
        self.available_shells = []
        self.default_shell = None
        
        # Get shell from environment
        shell_env = os.environ.get('SHELL', '')
        if shell_env:
            self.default_shell = Path(shell_env).name
        
        # Common shell locations by platform
        shell_paths = {
            PlatformType.LINUX: [
                '/bin/bash', '/usr/bin/bash',
                '/bin/zsh', '/usr/bin/zsh',
                '/bin/fish', '/usr/bin/fish',
                '/bin/sh', '/usr/bin/sh',
                '/bin/dash', '/usr/bin/dash',
                '/bin/tcsh', '/usr/bin/tcsh',
                '/bin/csh', '/usr/bin/csh',
                '/bin/ksh', '/usr/bin/ksh'
            ],
            PlatformType.MACOS: [
                '/bin/bash', '/usr/bin/bash',
                '/bin/zsh', '/usr/bin/zsh',
                '/usr/local/bin/bash',
                '/usr/local/bin/zsh',
                '/usr/local/bin/fish',
                '/bin/sh', '/usr/bin/sh',
                '/bin/tcsh', '/usr/bin/tcsh',
                '/bin/csh', '/usr/bin/csh'
            ],
            PlatformType.BSD: [
                '/bin/sh', '/usr/bin/sh',
                '/usr/local/bin/bash',
                '/usr/local/bin/zsh',
                '/bin/csh', '/usr/bin/csh',
                '/bin/tcsh', '/usr/bin/tcsh'
            ],
            PlatformType.WINDOWS: [
                'powershell.exe',
                'cmd.exe',
                'bash.exe'  # WSL or Git Bash
            ]
        }
        
        # Check for available shells
        paths_to_check = shell_paths.get(self.platform_type, [])
        
        for shell_path in paths_to_check:
            if self.platform_type == PlatformType.WINDOWS:
                # On Windows, check if command exists
                if shutil.which(shell_path):
                    shell_name = Path(shell_path).stem
                    self.available_shells.append((shell_path, shell_name))
            else:
                # On Unix-like systems, check if file exists and is executable
                if Path(shell_path).exists() and os.access(shell_path, os.X_OK):
                    shell_name = Path(shell_path).name
                    self.available_shells.append((shell_path, shell_name))
        
        # If no default shell detected, try to determine from available shells
        if not self.default_shell and self.available_shells:
            self.default_shell = self.available_shells[0][1]
        
        self.logger.debug(f"Available shells: {[s[1] for s in self.available_shells]}")
        self.logger.debug(f"Default shell: {self.default_shell}")
    
    def _detect_paths(self):
        """Detect important system paths."""
        self.home_dir = Path.home()
        self.config_dir = self._get_config_directory()
        self.cache_dir = self._get_cache_directory()
        self.data_dir = self._get_data_directory()
        self.temp_dir = Path(os.environ.get('TMPDIR', os.environ.get('TEMP', '/tmp')))
        
        # SSH directory
        self.ssh_dir = self.home_dir / '.ssh'
        if self.platform_type == PlatformType.WINDOWS:
            # Check for Windows SSH directory
            windows_ssh = self.home_dir / '.ssh'
            if not windows_ssh.exists():
                # Try %USERPROFILE%\.ssh
                windows_ssh = Path(os.environ.get('USERPROFILE', '')) / '.ssh'
            self.ssh_dir = windows_ssh
    
    def _get_config_directory(self) -> Path:
        """Get platform-appropriate configuration directory."""
        if self.platform_type == PlatformType.WINDOWS:
            return Path(os.environ.get('APPDATA', '')) / 'ashyterm'
        elif self.platform_type == PlatformType.MACOS:
            return self.home_dir / 'Library' / 'Application Support' / 'ashyterm'
        else:  # Linux, BSD, etc.
            xdg_config = os.environ.get('XDG_CONFIG_HOME')
            if xdg_config:
                return Path(xdg_config) / 'ashyterm'
            return self.home_dir / '.config' / 'ashyterm'
    
    def _get_cache_directory(self) -> Path:
        """Get platform-appropriate cache directory."""
        if self.platform_type == PlatformType.WINDOWS:
            return Path(os.environ.get('LOCALAPPDATA', '')) / 'ashyterm' / 'cache'
        elif self.platform_type == PlatformType.MACOS:
            return self.home_dir / 'Library' / 'Caches' / 'ashyterm'
        else:  # Linux, BSD, etc.
            xdg_cache = os.environ.get('XDG_CACHE_HOME')
            if xdg_cache:
                return Path(xdg_cache) / 'ashyterm'
            return self.home_dir / '.cache' / 'ashyterm'
    
    def _get_data_directory(self) -> Path:
        """Get platform-appropriate data directory."""
        if self.platform_type == PlatformType.WINDOWS:
            return Path(os.environ.get('LOCALAPPDATA', '')) / 'ashyterm' / 'data'
        elif self.platform_type == PlatformType.MACOS:
            return self.home_dir / 'Library' / 'Application Support' / 'ashyterm'
        else:  # Linux, BSD, etc.
            xdg_data = os.environ.get('XDG_DATA_HOME')
            if xdg_data:
                return Path(xdg_data) / 'ashyterm'
            return self.home_dir / '.local' / 'share' / 'ashyterm'
    
    def _detect_commands(self):
        """Detect available system commands."""
        self.commands = {}
        
        # Common commands to check
        command_list = [
            'ssh', 'sshpass', 'scp', 'sftp',
            'git', 'vim', 'nano', 'emacs',
            'grep', 'find', 'which', 'whereis',
            'ps', 'kill', 'top', 'htop'
        ]
        
        if self.platform_type == PlatformType.WINDOWS:
            command_list.extend(['powershell', 'cmd', 'wsl'])
        
        for cmd in command_list:
            cmd_path = shutil.which(cmd)
            if cmd_path:
                self.commands[cmd] = cmd_path
        
        self.logger.debug(f"Available commands: {list(self.commands.keys())}")
    
    def is_windows(self) -> bool:
        """Check if running on Windows."""
        return self.platform_type == PlatformType.WINDOWS
    
    def is_unix_like(self) -> bool:
        """Check if running on Unix-like system."""
        return self.platform_type in [PlatformType.LINUX, PlatformType.MACOS, PlatformType.BSD]
    
    def has_command(self, command: str) -> bool:
        """Check if a command is available."""
        return command in self.commands
    
    def get_command_path(self, command: str) -> Optional[str]:
        """Get full path to a command."""
        return self.commands.get(command)


class ShellDetector:
    """Utilities for shell detection and configuration."""
    
    def __init__(self, platform_info: PlatformInfo):
        self.platform_info = platform_info
        self.logger = get_logger('ashyterm.platform.shell')
    
    def get_default_shell(self) -> Tuple[str, ShellType]:
        """
        Get the default shell for the platform.
        
        Returns:
            Tuple of (shell_path, shell_type)
        """
        if self.platform_info.platform_type == PlatformType.WINDOWS:
            # On Windows, prefer PowerShell if available
            if self.platform_info.has_command('powershell'):
                return self.platform_info.get_command_path('powershell'), ShellType.POWERSHELL
            return 'cmd.exe', ShellType.CMD
        
        # For Unix-like systems
        if self.platform_info.available_shells:
            shell_path, shell_name = self.platform_info.available_shells[0]
            shell_type = self._detect_shell_type(shell_name)
            return shell_path, shell_type
        
        # Fallback
        return '/bin/sh', ShellType.SH
    
    def get_user_shell(self) -> Tuple[Optional[str], ShellType]:
        """
        Get the user's preferred shell from environment.
        
        Returns:
            Tuple of (shell_path, shell_type)
        """
        shell_env = os.environ.get('SHELL')
        if shell_env and Path(shell_env).exists():
            shell_name = Path(shell_env).name
            shell_type = self._detect_shell_type(shell_name)
            return shell_env, shell_type
        
        return self.get_default_shell()
    
    def _detect_shell_type(self, shell_name: str) -> ShellType:
        """Detect shell type from shell name."""
        shell_name = shell_name.lower()
        
        if 'bash' in shell_name:
            return ShellType.BASH
        elif 'zsh' in shell_name:
            return ShellType.ZSH
        elif 'fish' in shell_name:
            return ShellType.FISH
        elif shell_name in ['sh', 'dash']:
            return ShellType.SH
        elif 'tcsh' in shell_name:
            return ShellType.TCSH
        elif 'csh' in shell_name:
            return ShellType.CSH
        elif 'ksh' in shell_name:
            return ShellType.KSH
        elif 'powershell' in shell_name:
            return ShellType.POWERSHELL
        elif 'cmd' in shell_name:
            return ShellType.CMD
        else:
            return ShellType.UNKNOWN
    
    def get_shell_command_args(self, shell_type: ShellType) -> List[str]:
        """
        Get command arguments for launching a shell.
        
        Args:
            shell_type: Type of shell to launch
            
        Returns:
            List of command arguments
        """
        if shell_type == ShellType.POWERSHELL:
            return ['-NoExit', '-Command', '']
        elif shell_type == ShellType.CMD:
            return ['/K']
        elif shell_type in [ShellType.BASH, ShellType.ZSH]:
            return ['-i']  # Interactive shell
        elif shell_type == ShellType.FISH:
            return ['-i']
        else:
            return ['-i']  # Default to interactive for most shells


class PathManager:
    """Platform-aware path management utilities."""
    
    def __init__(self, platform_info: PlatformInfo):
        self.platform_info = platform_info
        self.logger = get_logger('ashyterm.platform.paths')
    
    def normalize_path(self, path: Union[str, Path]) -> Path:
        """
        Normalize path for the current platform.
        
        Args:
            path: Path to normalize
            
        Returns:
            Normalized Path object
        """
        if isinstance(path, str):
            path = Path(path)
        
        # Expand user home directory
        if str(path).startswith('~'):
            path = path.expanduser()
        
        # Resolve relative paths
        if not path.is_absolute():
            path = path.resolve()
        
        return path
    
    def get_ssh_config_path(self) -> Path:
        """Get SSH configuration file path."""
        return self.platform_info.ssh_dir / 'config'
    
    def get_ssh_known_hosts_path(self) -> Path:
        """Get SSH known_hosts file path."""
        return self.platform_info.ssh_dir / 'known_hosts'
    
    def get_ssh_key_paths(self) -> List[Path]:
        """Get common SSH key file paths."""
        ssh_dir = self.platform_info.ssh_dir
        
        key_names = [
            'id_rsa', 'id_dsa', 'id_ecdsa', 'id_ed25519',
            'id_rsa_legacy', 'github_rsa', 'gitlab_rsa'
        ]
        
        key_paths = []
        for key_name in key_names:
            key_path = ssh_dir / key_name
            if key_path.exists():
                key_paths.append(key_path)
        
        return key_paths
    
    def create_directory_safe(self, directory: Path, mode: int = 0o755) -> bool:
        """
        Safely create directory with platform-appropriate permissions.
        
        Args:
            directory: Directory to create
            mode: Directory permissions (ignored on Windows)
            
        Returns:
            True if directory was created or already exists
        """
        try:
            directory.mkdir(parents=True, exist_ok=True)
            
            # Set permissions on Unix-like systems
            if self.platform_info.is_unix_like():
                directory.chmod(mode)
            
            return True
            
        except Exception as e:
            self.logger.error(f"Failed to create directory {directory}: {e}")
            return False


class CommandBuilder:
    """Build platform-appropriate commands."""
    
    def __init__(self, platform_info: PlatformInfo):
        self.platform_info = platform_info
        self.logger = get_logger('ashyterm.platform.commands')
    
    def build_remote_command(self, command_type: str, hostname: str, username: Optional[str] = None,
                             key_file: Optional[str] = None, port: Optional[int] = None,
                             options: Optional[Dict[str, str]] = None) -> List[str]:
        """
        Builds a remote command (ssh, sftp) with platform-appropriate options.
        """
        if not self.platform_info.has_command(command_type):
            raise ConfigError(
                f"{command_type.upper()} command not found",
                details={'platform': self.platform_info.platform_type.value}
            )
        
        command_path = self.platform_info.get_command_path(command_type)
        cmd = [command_path]
        
        # Add options
        if options:
            for key, value in options.items():
                cmd.extend(['-o', f'{key}={value}'])
        
        # Add key file
        if key_file:
            cmd.extend(['-i', key_file])
        
        # Add port
        if port:
            # sftp uses -P (uppercase), ssh uses -p (lowercase)
            port_flag = '-P' if command_type == 'sftp' else '-p'
            cmd.extend([port_flag, str(port)])
        
        # Add connection target
        if username:
            cmd.append(f'{username}@{hostname}')
        else:
            cmd.append(hostname)
        
        return cmd
    
    def build_scp_command(self, source: str, destination: str,
                         username: Optional[str] = None, hostname: Optional[str] = None,
                         key_file: Optional[str] = None, port: Optional[int] = None,
                         recursive: bool = False) -> List[str]:
        """
        Build SCP command for file transfer.
        
        Args:
            source: Source path
            destination: Destination path
            username: SSH username
            hostname: SSH hostname
            key_file: SSH key file path
            port: SSH port
            recursive: Recursive transfer
            
        Returns:
            SCP command as list of arguments
        """
        if not self.platform_info.has_command('scp'):
            raise ConfigError(
                "SCP command not found",
                details={'platform': self.platform_info.platform_type.value}
            )
        
        scp_path = self.platform_info.get_command_path('scp')
        cmd = [scp_path]
        
        # Add options
        if recursive:
            cmd.append('-r')
        
        if key_file:
            cmd.extend(['-i', key_file])
        
        if port:
            cmd.extend(['-P', str(port)])
        
        # Build source and destination
        if hostname and username:
            if source.startswith('/') or ':' not in source:
                # Local to remote
                cmd.append(source)
                cmd.append(f'{username}@{hostname}:{destination}')
            else:
                # Remote to local
                cmd.append(f'{username}@{hostname}:{source}')
                cmd.append(destination)
        else:
            # Local copy
            cmd.extend([source, destination])
        
        return cmd


class EnvironmentManager:
    """Manage environment variables across platforms."""
    
    def __init__(self, platform_info: PlatformInfo):
        self.platform_info = platform_info
        self.logger = get_logger('ashyterm.platform.environment')
    
    def get_terminal_environment(self) -> Dict[str, str]:
        """
        Get environment variables for terminal sessions.
        
        Returns:
            Dictionary of environment variables
        """
        env = os.environ.copy()
        
        # Set terminal-specific variables
        env['TERM'] = 'xterm-256color'
        env['COLORTERM'] = 'truecolor'
        
        # Platform-specific adjustments
        if self.platform_info.platform_type == PlatformType.WINDOWS:
            # Windows-specific environment
            env['PYTHONIOENCODING'] = 'utf-8'
        else:
            # Unix-like systems - preserve user's locale, fallback to system default
            if 'LANG' not in env:
                # Try to detect system locale instead of forcing English
                import locale
                try:
                    system_locale = locale.getdefaultlocale()[0]
                    if system_locale:
                        env['LANG'] = f'{system_locale}.UTF-8'
                    else:
                        env['LANG'] = 'C.UTF-8'  # Neutral fallback instead of en_US
                except:
                    env['LANG'] = 'C.UTF-8'
            
            # Only set LC_ALL if absolutely necessary
            # Usually LANG is sufficient and LC_ALL can override user preferences
            if 'LC_ALL' not in env and 'LANG' not in os.environ:
                env['LC_ALL'] = env['LANG']
        
        return env
    
    def get_path_separator(self) -> str:
        """Get platform-appropriate path separator."""
        return ';' if self.platform_info.is_windows() else ':'
    
    def get_line_ending(self) -> str:
        """Get platform-appropriate line ending."""
        return '\r\n' if self.platform_info.is_windows() else '\n'


# Global platform info instance
_platform_info: Optional[PlatformInfo] = None


def get_platform_info() -> PlatformInfo:
    """
    Get the global platform information instance.
    
    Returns:
        PlatformInfo instance
    """
    global _platform_info
    if _platform_info is None:
        _platform_info = PlatformInfo()
    return _platform_info


def get_shell_detector() -> ShellDetector:
    """Get shell detector instance."""
    return ShellDetector(get_platform_info())


def get_path_manager() -> PathManager:
    """Get path manager instance."""
    return PathManager(get_platform_info())


def get_command_builder() -> CommandBuilder:
    """Get command builder instance."""
    return CommandBuilder(get_platform_info())


def get_environment_manager() -> EnvironmentManager:
    """Get environment manager instance."""
    return EnvironmentManager(get_platform_info())


# Convenience functions
def is_windows() -> bool:
    """Check if running on Windows."""
    return get_platform_info().is_windows()


def is_unix_like() -> bool:
    """Check if running on Unix-like system."""
    return get_platform_info().is_unix_like()


def get_default_shell() -> Tuple[str, ShellType]:
    """Get default shell for current platform."""
    return get_shell_detector().get_default_shell()


def get_config_directory() -> Path:
    """Get platform-appropriate configuration directory."""
    return get_platform_info().config_dir


def get_ssh_directory() -> Path:
    """Get SSH configuration directory."""
    return get_platform_info().ssh_dir


def has_command(command: str) -> bool:
    """Check if a command is available on the system."""
    return get_platform_info().has_command(command)


def normalize_path(path: Union[str, Path]) -> Path:
    """Normalize path for current platform."""
    return get_path_manager().normalize_path(path)


def ensure_directory_exists(directory: Union[str, Path], mode: int = 0o755) -> bool:
    """
    Ensure directory exists, creating it if necessary.
    
    Args:
        directory: Directory path to create
        mode: Directory permissions (ignored on Windows)
        
    Returns:
        True if directory exists or was created successfully
        
    Raises:
        ConfigError: If directory creation fails
    """
    try:
        path_manager = get_path_manager()
        directory_path = path_manager.normalize_path(directory)
        
        if directory_path.exists():
            if not directory_path.is_dir():
                raise ConfigError(
                    f"Path exists but is not a directory: {directory_path}",
                    details={'path': str(directory_path)},
                    user_message=f"Cannot create directory at {directory_path} - path exists but is not a directory"
                )
            return True
        
        return path_manager.create_directory_safe(directory_path, mode)
        
    except ConfigError:
        raise
    except Exception as e:
        logger = get_logger('ashyterm.platform.directory')
        logger.error(f"Failed to ensure directory exists: {directory}: {e}")
        raise ConfigError(
            f"Failed to create directory: {directory}",
            details={'path': str(directory), 'error': str(e)},
            user_message=f"Could not create directory: {directory}"
        )