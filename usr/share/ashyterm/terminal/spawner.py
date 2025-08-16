# terminal/spawner.py

import os
import signal
import threading
import time
from typing import Optional, Callable, Any, TYPE_CHECKING, Dict, List
from pathlib import Path

import gi

gi.require_version("Vte", "3.91")
from gi.repository import Vte, GLib

if TYPE_CHECKING:
    from ..sessions.models import SessionItem

from ..settings.config import SSH_CONNECT_TIMEOUT
from ..utils.logger import get_logger, log_terminal_event, log_error_with_context
from ..utils.exceptions import (
    TerminalSpawnError,
    SSHConnectionError,
    SSHAuthenticationError,
    SSHKeyError,
)
from ..utils.security import (
    validate_ssh_key_file,
    validate_ssh_hostname,
    HostnameValidator,
    InputSanitizer,
)
from ..utils.platform import (
    get_platform_info,
    get_command_builder,
    get_environment_manager,
    get_shell_detector,
    has_command,
    is_windows,
    ShellType,
)


class ProcessTracker:
    """Track spawned processes for proper cleanup."""

    def __init__(self):
        self.logger = get_logger("ashyterm.spawner.tracker")
        self._processes: Dict[int, Dict[str, Any]] = {}
        self._lock = threading.RLock()

    def register_process(self, pid: int, process_info: Dict[str, Any]) -> None:
        """Register a spawned process."""
        with self._lock:
            self._processes[pid] = {**process_info, "registered_at": time.time()}
            self.logger.debug(
                f"Process registered: PID={pid}, type={process_info.get('type', 'unknown')}"
            )

    def unregister_process(self, pid: int) -> bool:
        """Unregister a process."""
        with self._lock:
            if pid in self._processes:
                process_info = self._processes.pop(pid)
                self.logger.debug(
                    f"Process unregistered: PID={pid}, type={process_info.get('type', 'unknown')}"
                )
                return True
            return False

    def get_process_info(self, pid: int) -> Optional[Dict[str, Any]]:
        """Get process information."""
        with self._lock:
            return self._processes.get(pid, {}).copy()

    def get_all_processes(self) -> Dict[int, Dict[str, Any]]:
        """Get all tracked processes."""
        with self._lock:
            return self._processes.copy()

    def terminate_all(self) -> None:
        """Terminate all tracked processes robustly and cross-platform."""
        with self._lock:
            pids_to_terminate = list(self._processes.keys())
            if not pids_to_terminate:
                return

            self.logger.info(f"Terminating {len(pids_to_terminate)} tracked processes.")

            if is_windows():
                # No Windows, use taskkill para um encerramento mais confiável.
                for pid in pids_to_terminate:
                    try:
                        subprocess.run(
                            ["taskkill", "/F", "/PID", str(pid)],
                            check=False,
                            capture_output=True,
                        )
                        self.logger.debug(f"Sent taskkill to process {pid}")
                    except (OSError, FileNotFoundError):
                        self.logger.error("taskkill command not found.")
                        break  # Para de tentar se o comando não existir
                    finally:
                        self.unregister_process(pid)
            else:
                # Em sistemas Unix-like, tente SIGTERM primeiro, depois SIGKILL.
                # Etapa 1: Enviar SIGTERM para todos
                for pid in pids_to_terminate:
                    try:
                        os.kill(pid, signal.SIGTERM)
                        self.logger.debug(f"Sent SIGTERM to process {pid}")
                    except (OSError, ProcessLookupError):
                        # O processo já pode ter terminado
                        self.unregister_process(pid)

                # Dê um tempo para os processos encerrarem graciosamente
                time.sleep(0.2)

                # Etapa 2: Enviar SIGKILL para os que restaram
                remaining_pids = list(self._processes.keys())
                for pid in remaining_pids:
                    try:
                        os.kill(pid, signal.SIGKILL)
                        self.logger.warning(
                            f"Process {pid} did not respond to SIGTERM, sent SIGKILL."
                        )
                    except (OSError, ProcessLookupError):
                        pass  # O processo terminou no meio tempo
                    finally:
                        self.unregister_process(pid)

class ProcessSpawner:
    """Enhanced process spawner with comprehensive security and error handling."""

    def __init__(self):
        self.logger = get_logger("ashyterm.spawner")
        self.platform_info = get_platform_info()
        self.command_builder = get_command_builder()
        self.environment_manager = get_environment_manager()
        self.shell_detector = get_shell_detector()

        # Process tracking
        self.process_tracker = ProcessTracker()

        # Thread safety
        self._spawn_lock = threading.Lock()

        # Statistics
        self._stats = {
            "local_spawns": 0,
            "ssh_spawns": 0,
            "spawn_failures": 0,
            "processes_terminated": 0,
        }

        self.logger.info(
            f"Process spawner initialized on {self.platform_info.platform_type.value}"
        )

    def spawn_local_terminal(
        self,
        terminal: Vte.Terminal,
        callback: Optional[Callable] = None,
        user_data: Any = None,
    ) -> bool:
        """
        Spawn a local terminal session with enhanced platform support.

        Args:
            terminal: Vte.Terminal widget
            callback: Optional callback function for spawn completion
            user_data: Optional user data for callback

        Returns:
            True if spawn initiated successfully
        """
        with self._spawn_lock:
            try:
                self.logger.debug("Starting local terminal spawn")

                # Get platform-appropriate shell
                shell_path, shell_type = self.shell_detector.get_user_shell()
                shell_args = self.shell_detector.get_shell_command_args(shell_type)

                # Build command
                cmd = [shell_path] + shell_args

                # Get working directory
                working_dir = str(self.platform_info.home_dir)

                # Get environment
                env = self.environment_manager.get_terminal_environment()

                # Add VTE_VERSION to enable shell integration (OSC7)
                vte_version = (
                    Vte.get_major_version() * 10000
                    + Vte.get_minor_version() * 100
                    + Vte.get_micro_version()
                )
                env["VTE_VERSION"] = str(vte_version)
                self.logger.debug(f"Setting VTE_VERSION={env['VTE_VERSION']}")

                env_list = [f"{k}={v}" for k, v in env.items()]

                self.logger.debug(f"Spawning local terminal: {cmd}")

                # Validate command exists
                if not Path(shell_path).exists():
                    raise TerminalSpawnError(
                        shell_path, f"Shell not found: {shell_path}"
                    )

                # Spawn the process
                terminal.spawn_async(
                    Vte.PtyFlags.DEFAULT,
                    working_dir,
                    cmd,
                    env_list,
                    GLib.SpawnFlags.DEFAULT,
                    None,  # Child setup function
                    None,  # Child setup data
                    -1,  # Timeout (-1 for no timeout)
                    None,  # Cancellable
                    callback if callback else self._default_spawn_callback,
                    (user_data if user_data else "Local Terminal",),
                )

                self._stats["local_spawns"] += 1
                self.logger.info("Local terminal spawn initiated successfully")
                log_terminal_event(
                    "spawn_initiated", str(user_data), f"local shell: {shell_path}"
                )

                return True

            except Exception as e:
                self._stats["spawn_failures"] += 1
                self.logger.error(f"Local terminal spawn failed: {e}")
                log_error_with_context(e, "local terminal spawn", "ashyterm.spawner")

                if isinstance(e, TerminalSpawnError):
                    raise
                else:
                    raise TerminalSpawnError("local shell", str(e))

    def spawn_ssh_session(
        self,
        terminal: Vte.Terminal,
        session: "SessionItem",
        callback: Optional[Callable] = None,
        user_data: Any = None,
    ) -> bool:
        """
        Spawn an SSH terminal session with comprehensive security validation.

        Args:
            terminal: Vte.Terminal widget
            session: SessionItem with SSH configuration
            callback: Optional callback function for spawn completion
            user_data: Optional user data for callback

        Returns:
            True if spawn initiated successfully
        """
        with self._spawn_lock:
            if not session.is_ssh():
                raise TerminalSpawnError("ssh", "Session is not configured for SSH")

            try:
                self.logger.debug(f"Starting SSH spawn for session: {session.name}")

                # Validate session configuration
                self._validate_ssh_session(session)

                # Build SSH command with security considerations
                ssh_cmd = self._build_ssh_command_secure(session, terminal)

                if not ssh_cmd:
                    raise TerminalSpawnError("ssh", "Failed to build SSH command")

                # Get working directory and environment
                working_dir = str(self.platform_info.home_dir)
                env = self.environment_manager.get_terminal_environment()

                # Add VTE_VERSION to enable shell integration (OSC7)
                vte_version = (
                    Vte.get_major_version() * 10000
                    + Vte.get_minor_version() * 100
                    + Vte.get_micro_version()
                )
                env["VTE_VERSION"] = str(vte_version)
                self.logger.debug(
                    f"Setting VTE_VERSION={env['VTE_VERSION']} for SSH session"
                )

                env_list = [f"{k}={v}" for k, v in env.items()]

                self.logger.debug(f"SSH command: {' '.join(ssh_cmd)}")
                self.logger.debug(f"Working directory: {working_dir}")

                # Spawn the SSH process
                terminal.spawn_async(
                    Vte.PtyFlags.DEFAULT,
                    working_dir,
                    ssh_cmd,
                    env_list,
                    GLib.SpawnFlags.DEFAULT,
                    None,  # Child setup function
                    None,  # Child setup data
                    -1,  # Timeout
                    None,  # Cancellable
                    callback if callback else self._ssh_spawn_callback,
                    (user_data if user_data else session,),
                )

                self._stats["ssh_spawns"] += 1
                self.logger.info(f"SSH session spawn initiated for: {session.name}")
                log_terminal_event(
                    "spawn_initiated",
                    session.name,
                    f"SSH to {session.get_connection_string()}",
                )

                return True

            except Exception as e:
                self._stats["spawn_failures"] += 1
                self.logger.error(f"SSH session spawn failed for {session.name}: {e}")
                log_error_with_context(
                    e, f"SSH spawn for {session.name}", "ashyterm.spawner"
                )

                # Show error on terminal
                self._show_ssh_error_on_terminal(terminal, session, str(e))

                if isinstance(
                    e,
                    (
                        TerminalSpawnError,
                        SSHConnectionError,
                        SSHAuthenticationError,
                        SSHKeyError,
                    ),
                ):
                    raise
                else:
                    raise TerminalSpawnError("ssh", str(e))

    def _validate_ssh_session(self, session: "SessionItem") -> None:
        """
        Validate SSH session configuration with security checks.

        Args:
            session: SessionItem to validate

        Raises:
            Various SSH-related exceptions for different validation failures
        """
        # Validate hostname
        try:
            validate_ssh_hostname(session.host)
        except Exception as e:
            raise SSHConnectionError(session.host, f"Invalid hostname: {e}")

        # Validate authentication configuration
        if session.uses_key_auth():
            if not session.auth_value:
                raise SSHKeyError("", "SSH key path is empty")

            try:
                validate_ssh_key_file(session.auth_value)
            except Exception as e:
                raise SSHKeyError(session.auth_value, str(e))

        elif session.uses_password_auth():
            if not session.auth_value:
                self.logger.warning(
                    f"Password authentication configured but no password provided for {session.name}"
                )

        # Validate username
        if session.user:
            sanitized_user = InputSanitizer.sanitize_username(session.user)
            if sanitized_user != session.user:
                self.logger.warning(
                    f"Username sanitized for session {session.name}: '{session.user}' -> '{sanitized_user}'"
                )

        # Check if hostname resolves (non-blocking)
        try:
            ip = HostnameValidator.resolve_hostname(session.host, timeout=5.0)
            if ip:
                self.logger.debug(f"Hostname {session.host} resolves to {ip}")
                if HostnameValidator.is_private_ip(ip):
                    self.logger.info(f"Connecting to private IP: {ip}")
            else:
                self.logger.warning(f"Hostname {session.host} could not be resolved")
        except Exception as e:
            self.logger.debug(
                f"Hostname resolution check failed for {session.host}: {e}"
            )

    def _build_ssh_command_secure(
        self, session: "SessionItem", terminal: Vte.Terminal
    ) -> Optional[List[str]]:
        """
        Build SSH command with security considerations and platform compatibility.

        Args:
            session: SessionItem with SSH configuration
            terminal: Terminal widget for error messages

        Returns:
            List of command arguments or None if invalid
        """
        try:
            # Check if SSH is available
            if not has_command("ssh"):
                raise SSHConnectionError(
                    session.host, "SSH command not found on system"
                )

            # Use platform-aware command builder
            ssh_options = {
                "ConnectTimeout": str(SSH_CONNECT_TIMEOUT),
                "ServerAliveInterval": "30",
                "ServerAliveCountMax": "3",
                "StrictHostKeyChecking": "ask",
                "UserKnownHostsFile": str(self.platform_info.ssh_dir / "known_hosts"),
                "ControlMaster": "auto",
                "ControlPath": str(
                    self.platform_info.cache_dir / "ssh_control_%h_%p_%r"
                ),
                "ControlPersist": "600",
            }

            # Platform-specific SSH options
            if self.platform_info.is_windows():
                # Windows-specific SSH options
                ssh_options["PreferredAuthentications"] = "publickey,password"
            else:
                # Unix-specific SSH options
                ssh_options["Compression"] = "yes"
                ssh_options["TCPKeepAlive"] = "yes"

            # Build command using platform-aware builder
            cmd = self.command_builder.build_ssh_command(
                hostname=session.host,
                port=session.port if session.port != 22 else None,
                username=session.user if session.user else None,
                key_file=session.auth_value if session.uses_key_auth() else None,
                options=ssh_options,
            )

            # --- INÍCIO DA MODIFICAÇÃO ---
            # RESTAURADO: Injeta um comando remoto para configurar o PROMPT_COMMAND e emitir OSC7.
            # Isso é robusto e funciona mesmo se o servidor não tiver o vte.sh.
            try:
                osc7_setup = (
                    r"export PROMPT_COMMAND="
                    "'"
                    r'printf "\033]7;file://%s%s\007" "$(hostname)" "$PWD";'
                    "'"
                )
                remote_cmd = f"{osc7_setup}; exec $SHELL -l"

                if "-t" not in cmd:
                    cmd.insert(1, "-t")

                cmd.append(remote_cmd)
                self.logger.debug(
                    "Enhanced SSH command with OSC7 support for remote directory tracking."
                )

            except Exception as e:
                self.logger.debug(f"Could not enhance SSH with OSC7: {e}")
            # --- FIM DA MODIFICAÇÃO ---

            # Handle password authentication with sshpass
            if session.uses_password_auth() and session.auth_value:
                if has_command('sshpass'):
                    cmd = ['sshpass', '-p', session.auth_value] + cmd
                    self.logger.debug("Using sshpass for password authentication")
                else:
                    warning_msg = "sshpass not found. You'll need to enter password manually.\r\n"
                    self._feed_terminal_safely(terminal, warning_msg)
                    self.logger.warning("sshpass not available for password authentication")
            
            return cmd
            
        except Exception as e:
            self.logger.error(f"SSH command building failed: {e}")
            raise SSHConnectionError(session.host, f"Command building failed: {e}")
    
    def _feed_terminal_safely(self, terminal: Vte.Terminal, message: str) -> None:
        """
        Safely feed text to terminal with error handling.
        
        Args:
            terminal: Terminal widget
            message: Message to display
        """
        try:
            if terminal.get_realized():
                terminal.feed(message.encode('utf-8'))
        except Exception as e:
            self.logger.debug(f"Failed to feed message to terminal: {e}")
    
    def _show_ssh_error_on_terminal(self, terminal: Vte.Terminal, 
                                  session: "SessionItem", error_message: str) -> None:
        """
        Display SSH error message on terminal with enhanced formatting.
        
        Args:
            terminal: Terminal widget
            session: SessionItem that failed
            error_message: Error message to display
        """
        try:
            error_text = f"\r\n{'='*60}\r\n"
            error_text += f"SSH Connection Error for '{session.name}'\r\n"
            error_text += f"Host: {session.get_connection_string()}\r\n"
            error_text += f"Error: {error_message}\r\n"
            error_text += f"{'='*60}\r\n"
            error_text += "Please check your connection settings and try again.\r\n\r\n"
            
            self._feed_terminal_safely(terminal, error_text)
            
        except Exception as e:
            self.logger.error(f"Failed to show SSH error on terminal: {e}")
    
    def _default_spawn_callback(self, terminal: Vte.Terminal, pid: int, 
                               error: Optional[GLib.Error], user_data: Any = None) -> None:
        """
        Enhanced default callback for process spawn completion.
        
        Args:
            terminal: Terminal widget
            pid: Process ID (or -1 if failed)
            error: Error object if spawn failed
            user_data: User data passed to spawn (as tuple)
        """
        try:
            # Unpack user_data if it's a tuple
            if isinstance(user_data, tuple) and len(user_data) > 0:
                terminal_name = str(user_data[0])
            else:
                terminal_name = str(user_data) if user_data else "Terminal"
            
            if error:
                self.logger.error(f"Process spawn failed for {terminal_name}: {error.message}")
                log_terminal_event("spawn_failed", terminal_name, f"error: {error.message}")
                
                # Show user-friendly error message
                error_msg = f"\r\nFailed to start {terminal_name}:\r\n"
                error_msg += f"Error: {error.message}\r\n"
                error_msg += "Please check your system configuration.\r\n\r\n"
                self._feed_terminal_safely(terminal, error_msg)
                
            else:
                self.logger.info(f"Process spawned successfully for {terminal_name} with PID {pid}")
                log_terminal_event("spawned", terminal_name, f"PID {pid}")
                
                if pid > 0:
                    # Register process for tracking
                    process_info = {
                        'name': terminal_name,
                        'type': 'local',
                        'terminal': terminal
                    }
                    self.process_tracker.register_process(pid, process_info)
                    
        except Exception as e:
            self.logger.error(f"Spawn callback handling failed: {e}")
    
    def _ssh_spawn_callback(self, terminal: Vte.Terminal, pid: int,
                           error: Optional[GLib.Error], user_data: Any = None) -> None:
        """
        Enhanced callback for SSH process spawn completion.
        
        Args:
            terminal: Terminal widget
            pid: Process ID (or -1 if failed)
            error: Error object if spawn failed
            user_data: SessionItem or user data (as tuple)
        """
        try:
            # Unpack user_data if it's a tuple
            if isinstance(user_data, tuple) and len(user_data) > 0:
                actual_data = user_data[0]
            else:
                actual_data = user_data
            
            session_name = "SSH Session"
            session_host = "unknown"
            
            if hasattr(actual_data, 'name'):  # SessionItem
                session_name = actual_data.name
                session_host = actual_data.get_connection_string()
            elif actual_data:
                session_name = str(actual_data)
            
            if error:
                self.logger.error(f"SSH spawn failed for {session_name}: {error.message}")
                log_terminal_event("ssh_spawn_failed", session_name, f"error: {error.message}")
                
                # Determine error type and provide specific guidance
                error_guidance = self._get_ssh_error_guidance(error.message)

                error_msg = "\r\nSSH Connection Failed:\r\n"
                error_msg += f"Session: {session_name}\r\n"
                error_msg += f"Host: {session_host}\r\n"
                error_msg += f"Error: {error.message}\r\n"
                if error_guidance:
                    error_msg += f"Suggestion: {error_guidance}\r\n"
                error_msg += "\r\n"
                
                self._feed_terminal_safely(terminal, error_msg)
                
            else:
                self.logger.info(f"SSH process spawned successfully for {session_name} with PID {pid}")
                log_terminal_event("ssh_spawned", session_name, f"PID {pid} to {session_host}")
                
                if pid > 0:
                    # Register process for tracking
                    process_info = {
                        'name': session_name,
                        'type': 'ssh',
                        'terminal': terminal,
                        'session': actual_data if hasattr(actual_data, 'name') else None
                    }
                    self.process_tracker.register_process(pid, process_info)
                    
        except Exception as e:
            self.logger.error(f"SSH spawn callback handling failed: {e}")
    
    def _get_ssh_error_guidance(self, error_message: str) -> str:
        """
        Get user-friendly guidance based on SSH error message.
        
        Args:
            error_message: SSH error message
            
        Returns:
            User-friendly guidance string
        """
        error_lower = error_message.lower()
        
        if 'connection refused' in error_lower:
            return "Check if SSH service is running on the target host and the port is correct"
        elif 'permission denied' in error_lower:
            return "Check your username, password, or SSH key configuration"
        elif 'host key verification failed' in error_lower:
            return "The host key has changed. Remove the old key from known_hosts if this is expected"
        elif 'network is unreachable' in error_lower:
            return "Check your network connection and the hostname/IP address"
        elif 'no route to host' in error_lower:
            return "The host is not reachable. Check network connectivity and firewall settings"
        elif 'connection timed out' in error_lower:
            return "Connection timeout. The host may be down or firewalled"
        elif 'no such file or directory' in error_lower:
            return "SSH command not found or SSH key file missing"
        else:
            return "Check your SSH configuration and network connectivity"
    
    def cleanup_process(self, pid: int) -> None:
        """
        Clean up tracking for a terminated process.
        
        Args:
            pid: Process ID to clean up
        """
        try:
            success = self.process_tracker.unregister_process(pid)
            if success:
                self._stats['processes_terminated'] += 1
                self.logger.debug(f"Process cleanup completed for PID {pid}")
        except Exception as e:
            self.logger.error(f"Process cleanup failed for PID {pid}: {e}")
    
    def get_active_processes(self) -> Dict[int, Dict[str, Any]]:
        """
        Get dictionary of currently active processes.
        
        Returns:
            Dictionary mapping PIDs to process information
        """
        return self.process_tracker.get_all_processes()
    
    def terminate_all_processes(self) -> None:
        """Terminate all tracked processes and clean up."""
        try:
            self.logger.info("Terminating all spawned processes")
            self.process_tracker.terminate_all()
            self.logger.info("All processes terminated")
        except Exception as e:
            self.logger.error(f"Process termination failed: {e}")
    
    def get_statistics(self) -> Dict[str, Any]:
        """
        Get spawner statistics.
        
        Returns:
            Dictionary with spawner statistics
        """
        try:
            stats = self._stats.copy()
            stats.update({
                'active_processes': len(self.get_active_processes()),
                'platform': self.platform_info.platform_type.value,
                'ssh_available': has_command('ssh'),
                'sshpass_available': has_command('sshpass')
            })
            return stats
        except Exception as e:
            self.logger.error(f"Failed to get statistics: {e}")
            return {'error': str(e)}
    
    def test_ssh_connection(self, session: "SessionItem") -> bool:
        """
        Test SSH connection without spawning a terminal.
        
        Args:
            session: SessionItem to test
            
        Returns:
            True if connection test successful
        """
        try:
            self.logger.debug(f"Testing SSH connection for session: {session.name}")
            
            # Validate session first
            self._validate_ssh_session(session)
            
            # Build test command (just connection test)
            ssh_options = {
                'ConnectTimeout': '5',
                'BatchMode': 'yes',
                'StrictHostKeyChecking': 'no'
            }
            
            cmd = self.command_builder.build_ssh_command(
                hostname=session.host,
                username=session.user if session.user else None,
                key_file=session.auth_value if session.uses_key_auth() else None,
                options=ssh_options
            )
            
            # Add exit command to test connection only
            cmd.extend(['exit'])
            
            self.logger.debug(f"SSH test command: {' '.join(cmd)}")
            
            # This would require subprocess for actual testing
            # For now, just return validation result
            return True
            
        except Exception as e:
            self.logger.warning(f"SSH connection test failed for {session.name}: {e}")
            return False


# Global spawner instance
_spawner_instance: Optional[ProcessSpawner] = None
_spawner_lock = threading.Lock()


def get_spawner() -> ProcessSpawner:
    """
    Get the global ProcessSpawner instance (thread-safe singleton).
    
    Returns:
        ProcessSpawner singleton instance
    """
    global _spawner_instance
    
    if _spawner_instance is None:
        with _spawner_lock:
            if _spawner_instance is None:
                _spawner_instance = ProcessSpawner()
    
    return _spawner_instance


def cleanup_spawner() -> None:
    """Clean up the global spawner instance."""
    global _spawner_instance
    
    if _spawner_instance is not None:
        with _spawner_lock:
            if _spawner_instance is not None:
                _spawner_instance.terminate_all_processes()
                _spawner_instance = None