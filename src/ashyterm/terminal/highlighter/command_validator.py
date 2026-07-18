# ashyterm/terminal/highlighter/command_validator.py
"""
Command validator for shell input highlighting.

Checks whether a command exists in $PATH, as a shell builtin, or as an alias/function.
Uses a cached set of available commands that refreshes periodically.
"""

from __future__ import annotations

import os
import time
from typing import Optional, Set


# Shell builtins common to bash/zsh/sh
_SHELL_BUILTINS: frozenset[str] = frozenset(
    {
        ".",
        ":",
        "[",
        "alias",
        "bg",
        "bind",
        "break",
        "builtin",
        "caller",
        "case",
        "cd",
        "command",
        "compgen",
        "complete",
        "compopt",
        "continue",
        "declare",
        "dirs",
        "disown",
        "do",
        "done",
        "echo",
        "elif",
        "else",
        "enable",
        "esac",
        "eval",
        "exec",
        "exit",
        "export",
        "false",
        "fc",
        "fg",
        "fi",
        "for",
        "function",
        "getopts",
        "hash",
        "help",
        "history",
        "if",
        "in",
        "jobs",
        "kill",
        "let",
        "local",
        "logout",
        "mapfile",
        "popd",
        "printf",
        "pushd",
        "pwd",
        "read",
        "readarray",
        "readonly",
        "return",
        "select",
        "set",
        "shift",
        "shopt",
        "source",
        "suspend",
        "test",
        "then",
        "time",
        "times",
        "trap",
        "true",
        "type",
        "typeset",
        "ulimit",
        "umask",
        "unalias",
        "unset",
        "until",
        "wait",
        "while",
    }
)

# Cache refresh interval in seconds
_CACHE_TTL: float = 30.0


class CommandValidator:
    """Validates whether a command name is executable."""

    _instance: Optional[CommandValidator] = None
    _instance_lock = __import__("threading").Lock()

    def __init__(self) -> None:
        self._path_commands: Set[str] = set()
        self._last_refresh: float = 0.0
        self._dir_mtimes: dict[str, float] = {}
        self._enabled: bool = True
        self._refresh_path_cache()

    @classmethod
    def get_instance(cls) -> CommandValidator:
        """Get or create the singleton instance (thread-safe)."""
        if cls._instance is None:
            with cls._instance_lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    @property
    def enabled(self) -> bool:
        return self._enabled

    @enabled.setter
    def enabled(self, value: bool) -> None:
        self._enabled = value

    def is_valid_command(self, command: str) -> bool:
        """
        Check if a command name is valid (exists in $PATH or is a builtin).

        Args:
            command: The command name to check (e.g., "ls", "grep", "cd").

        Returns:
            True if the command is likely valid, False if not found.
        """
        if not command or not self._enabled:
            return True  # Don't flag empty or when disabled

        # Shell builtins are always valid
        if command in _SHELL_BUILTINS:
            return True

        # Relative/absolute paths — check file existence
        if "/" in command:
            return os.path.isfile(command) and os.access(command, os.X_OK)

        # Refresh cache if stale
        now = time.monotonic()
        if now - self._last_refresh > _CACHE_TTL:
            self._refresh_path_cache()

        return command in self._path_commands

    def _refresh_path_cache(self) -> None:
        """Rebuild the set of commands available in $PATH.

        Uses directory mtime to skip unchanged directories, avoiding
        a full rescan on every TTL expiry.
        """
        path_var = os.environ.get("PATH", "")
        dirs = [d for d in path_var.split(os.pathsep) if d]
        new_mtimes = self._collect_path_mtimes(dirs)
        needs_full_scan = new_mtimes != self._dir_mtimes

        if not needs_full_scan and self._path_commands:
            # No changes detected, keep existing cache
            self._last_refresh = time.monotonic()
            return

        self._path_commands = self._scan_path_commands(dirs)
        self._dir_mtimes = new_mtimes
        self._last_refresh = time.monotonic()

    @staticmethod
    def _collect_path_mtimes(dirs: list[str]) -> dict[str, float]:
        mtimes: dict[str, float] = {}
        for directory in dirs:
            try:
                mtimes[directory] = os.stat(directory).st_mtime
            except OSError:
                mtimes[directory] = 0.0
        return mtimes

    @staticmethod
    def _scan_path_commands(dirs: list[str]) -> Set[str]:
        commands: Set[str] = set()
        for directory in dirs:
            try:
                entries = os.scandir(directory)
            except OSError:
                continue
            with entries:
                for entry in entries:
                    try:
                        if entry.is_file(follow_symlinks=True):
                            commands.add(entry.name)
                    except OSError:
                        continue
        return commands

    def invalidate_cache(self) -> None:
        """Force cache refresh on next lookup."""
        self._last_refresh = 0.0
