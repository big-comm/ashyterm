# ashyterm/utils/osc7.py

from pathlib import Path
from typing import NamedTuple

from .logger import get_logger


class OSC7Info(NamedTuple):
    """Information extracted from OSC7 sequence."""

    hostname: str
    path: str
    display_path: str


class OSC7Parser:
    """Parser for OSC7 escape sequences."""

    def __init__(self):
        """Initialize OSC7 parser."""
        self.logger = get_logger("ashyterm.utils.osc7")
        self._home_path = str(Path.home())

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
