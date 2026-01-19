# ashyterm/utils/ssh_config_parser.py

import glob
import os
import shlex
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Set

from .logger import get_logger


@dataclass(slots=True)
class SSHConfigHost:
    """Lightweight representation of a host entry inside ssh_config."""

    alias: str
    hostname: Optional[str] = None
    user: Optional[str] = None
    port: Optional[int] = None
    identity_file: Optional[str] = None
    forward_x11: Optional[bool] = None


class SSHConfigParser:
    """Simple parser for OpenSSH-style config files."""

    def __init__(self) -> None:
        self.logger = get_logger("ashyterm.utils.sshconfig")
        self._entries: List[SSHConfigHost] = []
        self._visited: Set[Path] = set()

    def parse(self, config_path: Path) -> List[SSHConfigHost]:
        """Parses the provided ssh_config file and returns host entries."""
        self._entries.clear()
        self._visited.clear()

        expanded_path = config_path.expanduser()
        self._parse_file(expanded_path)
        return self._entries

    # --- Internal helpers -------------------------------------------------

    def _resolve_config_path(self, path: Path) -> Optional[Path]:
        """Resolve and validate SSH config file path."""
        try:
            resolved = path.resolve()
        except FileNotFoundError:
            self.logger.warning(f"SSH config path does not exist: {path}")
            return None

        if resolved in self._visited:
            return None
        if not resolved.is_file():
            self.logger.warning(f"SSH config path is not a file: {path}")
            return None
        return resolved

    def _process_config_line(
        self,
        keyword: str,
        values: List[str],
        current_patterns: List[str],
        current_options: Dict[str, str],
        directory: Path,
    ) -> tuple[List[str], Dict[str, str], bool]:
        """Process a single config line and return updated state."""
        if keyword == "match":
            self._flush_hosts(current_patterns, current_options)
            return [], {}, True  # stop_processing = True
        elif keyword == "host":
            self._flush_hosts(current_patterns, current_options)
            return values, {}, False
        elif keyword == "include":
            self._flush_hosts(current_patterns, current_options)
            self._handle_include(values, directory)
            return [], {}, False
        else:
            if current_patterns and values:
                current_options[keyword] = " ".join(values)
            return current_patterns, current_options, False

    def _parse_file(self, path: Path) -> None:
        resolved = self._resolve_config_path(path)
        if resolved is None:
            return

        self._visited.add(resolved)
        directory = resolved.parent

        current_patterns: List[str] = []
        current_options: Dict[str, str] = {}

        with resolved.open("r", encoding="utf-8", errors="ignore") as handle:
            for raw_line in handle:
                line = raw_line.strip()
                if not line or line.startswith("#"):
                    continue

                tokens = self._tokenize(line)
                if not tokens:
                    continue

                keyword = tokens[0].lower()
                values = tokens[1:]

                current_patterns, current_options, stop = self._process_config_line(
                    keyword, values, current_patterns, current_options, directory
                )
                if stop:
                    break

        self._flush_hosts(current_patterns, current_options)

    def _handle_include(self, patterns: Iterable[str], base_dir: Path) -> None:
        for pattern in patterns:
            expanded = self._expand_path(pattern, base_dir)
            for match in glob.glob(str(expanded), recursive=True):
                self._parse_file(Path(match))

    def _flush_hosts(self, patterns: List[str], options: Dict[str, str]) -> None:
        if not patterns:
            return

        for alias in patterns:
            if not alias or any(ch in alias for ch in ["*", "?", "!"]):
                # Skip wildcard or negated hosts
                continue

            entry = SSHConfigHost(alias=alias)
            if hostname := options.get("hostname"):
                entry.hostname = hostname
            if user := options.get("user"):
                entry.user = user
            if port := options.get("port"):
                try:
                    entry.port = int(port)
                except ValueError:
                    self.logger.debug(
                        f"Invalid port '{port}' for host '{alias}' in ssh config."
                    )
            if identity := options.get("identityfile"):
                entry.identity_file = identity
            if forward := options.get("forwardx11"):
                entry.forward_x11 = forward.lower() in {"yes", "true", "on"}

            self._entries.append(entry)

    @staticmethod
    def _expand_path(path_str: str, base_dir: Path) -> Path:
        expanded = Path(os.path.expanduser(path_str))
        if not expanded.is_absolute():
            expanded = base_dir / expanded
        return expanded

    @staticmethod
    def _tokenize(line: str) -> List[str]:
        lexer = shlex.shlex(line, posix=True)
        lexer.commenters = "#"
        lexer.whitespace_split = True
        return list(lexer)
