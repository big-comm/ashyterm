# ashyterm/utils/osc7.py

import re
from pathlib import Path
from typing import NamedTuple, Optional
from urllib.parse import unquote

from .logger import get_logger


class OSC7Info(NamedTuple):
    """Information extracted from OSC7 sequence."""

    hostname: str
    path: str
    display_path: str


class OSC7Parser:
    """Parser for OSC7 escape sequences."""

    OSC7_PATTERN = re.compile(
        rb"\x1b\]7;file://([^/\x07\x1b]*)(/?[^\x07\x1b]*?)(?:\x07|\x1b\\)",
        re.IGNORECASE,
    )

    def __init__(self):
        """Initialize OSC7 parser."""
        self.logger = get_logger("ashyterm.utils.osc7")
        self._home_path = str(Path.home())

    def parse_osc7(self, data: bytes) -> Optional[OSC7Info]:
        """
        Parse OSC7 escape sequences from terminal output.

        Args:
            data: Raw bytes from terminal output

        Returns:
            OSC7Info if valid sequence found, None otherwise
        """
        try:
            matches = self.OSC7_PATTERN.findall(data)
            if not matches:
                return None

            hostname_bytes, path_bytes = matches[-1]

            try:
                hostname = hostname_bytes.decode("utf-8", errors="replace")
                raw_path = path_bytes.decode("utf-8", errors="replace")
            except UnicodeDecodeError as e:
                self.logger.warning(f"Failed to decode OSC7 sequence: {e}")
                return None

            try:
                decoded_path = unquote(raw_path)
            except Exception as e:
                self.logger.warning(f"Failed to URL decode path '{raw_path}': {e}")
                decoded_path = raw_path

            normalized_path = self._normalize_path(decoded_path)
            if not normalized_path:
                return None

            display_path = self._create_display_path(normalized_path)

            return OSC7Info(
                hostname=hostname or "localhost",
                path=normalized_path,
                display_path=display_path,
            )
        except Exception as e:
            self.logger.error(f"OSC7 parsing failed: {e}")
            return None

    def _normalize_path(self, path: str) -> Optional[str]:
        """Normalize and validate the path from OSC7."""
        try:
            if not path or path == "/":
                return "/"
            normalized = path.rstrip("/")
            if not normalized:
                normalized = "/"
            if not normalized.startswith("/"):
                self.logger.warning(f"OSC7 path is not absolute: '{path}'")
                return None
            if len(normalized) > 4096:
                self.logger.warning(f"OSC7 path too long: {len(normalized)} chars")
                return None
            return normalized
        except Exception as e:
            self.logger.error(f"Path normalization failed for '{path}': {e}")
            return None

    def _create_display_path(self, path: str) -> str:
        """
        Create a user-friendly display version of the path.

        Args:
            path: Normalized absolute path

        Returns:
            Display-friendly path string
        """
        try:
            if not path or path == "/":
                return "/"

            if path.startswith(self._home_path):
                # CORREÇÃO: Retornar o caminho completo se for exatamente o home,
                # e usar '~' apenas para subdiretórios.
                if path == self._home_path:
                    return path
                else:
                    return "~" + path[len(self._home_path) :]

            path_parts = path.split("/")
            if len(path_parts) > 4:
                return ".../" + "/".join(path_parts[-3:])
            return path
        except Exception as e:
            self.logger.warning(f"Display path creation failed for '{path}': {e}")
            return path
