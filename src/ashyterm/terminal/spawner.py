# ashyterm/terminal/spawner.py

"""Process spawner orchestrator — delegates to mixin classes."""

import threading
from typing import Optional

from ..settings.manager import get_settings_manager
from ..utils.logger import get_logger
from .local_spawn_mixin import LocalSpawnMixin
from .process_tracker import ProcessTracker
from .ssh_spawn_mixin import SSHSpawnMixin
from ..utils.logger import log_swallowed_exception

LOGGER_NAME_SPAWNER = "ashyterm.spawner"


def __getattr__(name: str):
    """Lazy re-exports for backward compatibility."""
    if name == "SSHConnectionChecker":
        from .ssh_control import SSHConnectionChecker as _Cls

        return _Cls
    if name == "cleanup_stale_sockets":
        from .ssh_control import cleanup_stale_sockets as _fn

        return _fn
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


class ProcessSpawner(LocalSpawnMixin, SSHSpawnMixin):
    """Enhanced process spawner with comprehensive security and error handling."""

    def __init__(self, settings_manager=None):
        self.logger = get_logger(LOGGER_NAME_SPAWNER)
        from ..utils.platform import get_command_builder, get_environment_manager, get_platform_info

        self.platform_info = get_platform_info()
        self.command_builder = get_command_builder()
        self.environment_manager = get_environment_manager()
        self.process_tracker = ProcessTracker()
        self.settings_manager = settings_manager or get_settings_manager()
        self._spawn_lock = threading.Lock()
        self._last_sshpass_file: Optional[str] = None
        self.logger.info("Process launcher initialized on Linux")


# Module-level singleton instances
_spawner_instance: Optional[ProcessSpawner] = None
_spawner_lock = threading.Lock()
_checker_instance = None
_checker_lock = threading.Lock()


def get_spawner() -> ProcessSpawner:
    """Get the singleton ProcessSpawner instance."""
    global _spawner_instance
    if _spawner_instance is None:
        with _spawner_lock:
            if _spawner_instance is None:
                _spawner_instance = ProcessSpawner()
    return _spawner_instance


def get_ssh_connection_checker():
    """Get the singleton SSH connection checker instance."""
    global _checker_instance
    if _checker_instance is None:
        with _checker_lock:
            if _checker_instance is None:
                from .ssh_control import SSHConnectionChecker

                _checker_instance = SSHConnectionChecker()
    return _checker_instance


def cleanup_spawner() -> None:
    """Clean up spawner resources and terminate tracked processes.

    Also sweeps stale ashyterm_zsh_* ZDOTDIR directories that might have
    leaked if a previous session crashed before the spawn callback ran.
    """
    global _spawner_instance, _checker_instance

    if _checker_instance is not None:
        try:
            _checker_instance.cleanup_stale_sockets()
        except Exception as exc:
            log_swallowed_exception(exc)

    if _spawner_instance is not None:
        with _spawner_lock:
            if _spawner_instance is not None:
                _spawner_instance.process_tracker.terminate_all()
                _spawner_instance = None

    with _checker_lock:
        _checker_instance = None

    _sweep_orphan_zsh_tmpdirs()


def _sweep_orphan_zsh_tmpdirs() -> None:
    """Remove leftover /tmp/ashyterm_zsh_* directories from crashed sessions."""
    import shutil
    import tempfile
    from pathlib import Path

    logger = get_logger(LOGGER_NAME_SPAWNER)
    tmp_root = Path(tempfile.gettempdir())
    try:
        candidates = list(tmp_root.glob("ashyterm_zsh_*"))
    except OSError as exc:
        logger.debug(f"Could not scan {tmp_root} for orphan zsh dirs: {exc}")
        return

    for path in candidates:
        try:
            if path.is_dir():
                shutil.rmtree(path, ignore_errors=True)
        except OSError as exc:
            logger.debug(f"Could not remove orphan zsh dir {path}: {exc}")
