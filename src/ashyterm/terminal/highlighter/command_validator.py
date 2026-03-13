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
_SHELL_BUILTINS: frozenset[str] = frozenset({
    ".", ":", "[", "alias", "bg", "bind", "break", "builtin", "caller",
    "case", "cd", "command", "compgen", "complete", "compopt", "continue",
    "declare", "dirs", "disown", "do", "done", "echo", "elif", "else",
    "enable", "esac", "eval", "exec", "exit", "export", "false", "fc",
    "fg", "fi", "for", "function", "getopts", "hash", "help", "history",
    "if", "in", "jobs", "kill", "let", "local", "logout", "mapfile",
    "popd", "printf", "pushd", "pwd", "read", "readarray", "readonly",
    "return", "select", "set", "shift", "shopt", "source", "suspend",
    "test", "then", "time", "times", "trap", "true", "type", "typeset",
    "ulimit", "umask", "unalias", "unset", "until", "wait", "while",
})

# Cache refresh interval in seconds
_CACHE_TTL: float = 30.0


class CommandValidator:
    """Validates whether a command name is executable."""

    _instance: Optional[CommandValidator] = None

    def __init__(self) -> None:
        self._path_commands: Set[str] = set()
        self._last_refresh: float = 0.0
        self._enabled: bool = True
        self._refresh_path_cache()

    @classmethod
    def get_instance(cls) -> CommandValidator:
        """Get or create the singleton instance."""
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
        """Rebuild the set of commands available in $PATH."""
        commands: Set[str] = set()
        path_var = os.environ.get("PATH", "")

        for directory in path_var.split(os.pathsep):
            if not directory:
                continue
            try:
                with os.scandir(directory) as entries:
                    for entry in entries:
                        try:
                            if entry.is_file(follow_symlinks=True):
                                commands.add(entry.name)
                        except OSError:
                            continue
            except (OSError, PermissionError):
                continue

        self._path_commands = commands
        self._last_refresh = time.monotonic()

    def invalidate_cache(self) -> None:
        """Force cache refresh on next lookup."""
        self._last_refresh = 0.0

