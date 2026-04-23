# ashyterm/terminal/ssh_control.py

import os
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from ..utils.logger import get_logger
from ..utils.logger import log_swallowed_exception

if TYPE_CHECKING:
    from ..sessions.models import SessionItem
    from .spawner import ProcessSpawner


class SSHConnectionChecker:
    """Check and manage existing SSH ControlMaster connections.
    Uses existing ControlPath sockets created by ProcessSpawner.
    """

    def __init__(self, spawner: Optional["ProcessSpawner"] = None):
        self.logger = get_logger("ashyterm.ssh_checker")
        self._spawner = spawner

    @property
    def spawner(self) -> "ProcessSpawner":
        """Get spawner instance, using global if none provided."""
        if self._spawner is None:
            from .spawner import get_spawner

            self._spawner = get_spawner()
        return self._spawner

    def is_master_active(self, session: "SessionItem") -> bool:
        """Check if ControlMaster connection is active for session."""
        control_path = self.spawner._get_ssh_control_path(session)

        if not Path(control_path).exists():
            return False

        user = session.user or os.getlogin()
        cmd = ["ssh", "-O", "check", "-S", control_path, f"{user}@{session.host}"]

        try:
            result = subprocess.run(cmd, capture_output=True, timeout=5, text=True)
            is_active = result.returncode == 0
            self.logger.debug(
                f"ControlMaster check for {session.name}: "
                f"{'active' if is_active else 'inactive'}"
            )
            return is_active
        except subprocess.TimeoutExpired:
            self.logger.warning(f"Timeout checking ControlMaster for {session.name}")
            return False
        except Exception as e:
            self.logger.debug(f"Error checking ControlMaster for {session.name}: {e}")
            return False

    def get_master_info(self, session: "SessionItem") -> Optional[Dict[str, Any]]:
        """Get info about active ControlMaster connection."""
        if not self.is_master_active(session):
            return None

        control_path = self.spawner._get_ssh_control_path(session)
        socket_stat = Path(control_path).stat()

        return {
            "host": session.host,
            "user": session.user or os.getlogin(),
            "port": session.port or 22,
            "control_path": control_path,
            "active": True,
            "socket_created": socket_stat.st_ctime,
            "can_quick_reconnect": True,
        }

    def terminate_master(self, session: "SessionItem") -> bool:
        """Gracefully terminate a ControlMaster connection."""
        control_path = self.spawner._get_ssh_control_path(session)

        if not Path(control_path).exists():
            return True

        user = session.user or os.getlogin()
        cmd = ["ssh", "-O", "exit", "-S", control_path, f"{user}@{session.host}"]

        try:
            result = subprocess.run(cmd, capture_output=True, timeout=10, text=True)

            if result.returncode == 0:
                self.logger.info(
                    f"Successfully terminated ControlMaster for {session.name}"
                )
                return True
            else:
                self.logger.warning(
                    f"Failed to terminate ControlMaster for {session.name}: "
                    f"{result.stderr}"
                )
                return False

        except subprocess.TimeoutExpired:
            self.logger.warning(f"Timeout terminating ControlMaster for {session.name}")
            return False
        except Exception as e:
            self.logger.error(
                f"Error terminating ControlMaster for {session.name}: {e}"
            )
            return False

    def terminate_all_masters(self, sessions: List["SessionItem"]) -> int:
        """Terminate all ControlMaster connections for given sessions."""
        terminated = 0
        for session in sessions:
            if session.is_ssh() and self.is_master_active(session):
                if self.terminate_master(session):
                    terminated += 1
        return terminated

    def cleanup_stale_sockets(self) -> int:
        """Remove stale control socket files without active connections."""
        cache_dir = self.spawner.platform_info.cache_dir
        cleaned = 0

        if not cache_dir.exists():
            return 0

        for socket_file in cache_dir.glob("ssh_control_*"):
            if socket_file.is_socket():
                try:
                    result = subprocess.run(
                        ["ssh", "-O", "check", "-S", str(socket_file), "dummy"],
                        capture_output=True,
                        timeout=2,
                    )
                    if result.returncode != 0:
                        socket_file.unlink(missing_ok=True)
                        cleaned += 1
                        self.logger.debug(f"Cleaned stale socket: {socket_file}")
                except subprocess.TimeoutExpired:
                    socket_file.unlink(missing_ok=True)
                    cleaned += 1
                except Exception as exc:
                    log_swallowed_exception(exc)

        if cleaned > 0:
            self.logger.info(f"Cleaned up {cleaned} stale SSH control sockets")

        return cleaned


def cleanup_stale_sockets() -> int:
    """Module-level helper — clean stale SSH control sockets."""
    from .spawner import get_ssh_connection_checker

    checker = get_ssh_connection_checker()
    return checker.cleanup_stale_sockets()
