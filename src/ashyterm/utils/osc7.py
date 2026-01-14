# ashyterm/utils/osc7.py

from pathlib import Path
from typing import NamedTuple, Optional
from urllib.parse import unquote, urlparse

from .logger import get_logger


class OSC7Info(NamedTuple):
    """Information extracted from OSC7 sequence."""

    hostname: str
    path: str
    display_path: str


def parse_directory_uri(
    uri: str, parser: Optional["OSC7Parser"] = None
) -> Optional[OSC7Info]:
    """
    Parse a file:// URI and return OSC7Info.

    This is a common utility function to avoid code duplication in:
    - terminal/manager.py (_on_directory_uri_changed)
    - utils/osc7_tracker.py (_handle_directory_uri_change)

    Args:
        uri: The file:// URI to parse
        parser: Optional OSC7Parser instance for creating display paths

    Returns:
        OSC7Info if valid file URI, None otherwise
    """
    if not uri:
        return None

    try:
        parsed = urlparse(uri)
        if parsed.scheme != "file":
            return None

        path = unquote(parsed.path)
        hostname = parsed.hostname or "localhost"

        if parser:
            display_path = parser._create_display_path(path)
        else:
            # Fallback display path creation
            display_path = path

        return OSC7Info(hostname=hostname, path=path, display_path=display_path)
    except Exception:
        return None


# Shell snippet for detecting hostname (used by spawner for OSC7 emission)
OSC7_HOST_DETECTION_SNIPPET = (
    'if [ -z "$ASHYTERM_OSC7_HOST" ]; then '
    "if command -v hostname >/dev/null 2>&1; then "
    'ASHYTERM_OSC7_HOST="$(hostname)"; '
    'elif [ -n "$HOSTNAME" ]; then '
    'ASHYTERM_OSC7_HOST="$HOSTNAME"; '
    "elif command -v uname >/dev/null 2>&1; then "
    'ASHYTERM_OSC7_HOST="$(uname -n)"; '
    "else "
    'ASHYTERM_OSC7_HOST="unknown"; '
    "fi; "
    "fi;"
)


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
